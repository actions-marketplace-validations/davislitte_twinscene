"""Edits a Unity scene from TwinScene fields: creates, changes and deletes GameObjects, components and placed models.

Only what TwinScene understands is touched; every other line of the scene stays as it was.
"""

from __future__ import annotations

import math
import re
import random
from pathlib import Path

from .. import coords
from .. import models
from ..model import colliders, is_collider_group
from ..project import UnityProject
from . import reader as R
from . import yaml
from .yaml import FlowMap, ref

TEMPLATES = Path(__file__).with_name("templates.yaml")
MONOBEHAVIOUR, PART_SCRIPT_GUID, part_kind = R.MONOBEHAVIOUR, R.PART_SCRIPT_GUID, R.part_kind
ROOT_GO_ID, ROOT_TRANSFORM_ID = R.ROOT_GO_ID, R.ROOT_TRANSFORM_ID
PRIMITIVE_IDS = {v: k for k, v in R.BUILTIN_MESHES.items()}
DEFAULT_MATERIAL = FlowMap(fileID=10303, guid=R.BUILTIN_DEFAULT, type=0)
COLLIDER_CLASS = {"box": R.BOXCOLLIDER, "sphere": R.SPHERECOLLIDER, "capsule": R.CAPSULECOLLIDER}
LIGHT_CODES = {"spot": 0, "directional": 1, "point": 2}

_templates: yaml.Document | None = None


def _template(class_id: int, file_id: int) -> yaml.UObject:
    global _templates
    if _templates is None:
        _templates = yaml.load(TEMPLATES)
    src = next(o for o in _templates.objects if o.class_id == class_id)
    lines = list(src.lines)
    lines[0] = f"--- !u!{class_id} &{file_id}"
    return yaml.parse_object(lines)


def euler_hint(q) -> FlowMap:
    """Unity's inspector angles for a rotation (Unity applies Z, then X, then Y)."""
    x, y, z, w = q
    m12 = 2 * (y * z - x * w)
    ex = math.degrees(math.asin(max(-1.0, min(1.0, -m12))))
    if abs(m12) < 0.9999999:
        ey = math.degrees(math.atan2(2 * (x * z + y * w), 1 - 2 * (x * x + y * y)))
        ez = math.degrees(math.atan2(2 * (x * y + z * w), 1 - 2 * (x * x + z * z)))
    else:
        ey = math.degrees(math.atan2(-(2 * (x * z - y * w)), 1 - 2 * (y * y + z * z)))
        ez = 0.0
    return FlowMap(x=_tidy(ex), y=_tidy(ey), z=_tidy(ez))


def _tidy(a: float) -> float:
    a = round(a, 4)
    return 0.0 if a == 0 else a


def _vec(v, keys="xyz") -> FlowMap:
    return FlowMap({k: float(c) for k, c in zip(keys, v)})


def unit_primitive_scale(prim: str, size) -> tuple[float, float, float]:
    """Godot-style primitive size -> the scale that makes Unity's fixed-size built-in mesh match."""
    if prim == "box":
        return tuple(size)
    if prim == "sphere":
        r, h = size
        return (2 * r, h, 2 * r)
    if prim in ("capsule", "cylinder"):
        r, h = size
        return (2 * r, h / 2, 2 * r)
    if prim == "plane":
        return (size[0] / 10, 1.0, size[1] / 10)
    return (size[0], size[1], 1.0)  # quad


class UnityEditor:
    def __init__(self, doc: yaml.Document, project: UnityProject, rng: random.Random | None = None,
                 prefab: bool = False):
        """prefab=True edits a .prefab file: one root object, and no SceneRoots list."""
        self.doc = doc
        self.project = project
        self.rng = rng or random.Random()
        self.prefab = prefab
        self.warnings: list[str] = []

    # ------------------------------------------------------------ lookups

    def obj(self, key: str) -> yaml.UObject | None:
        return self.doc.by_id(int(key[2:])) if key and key.startswith("u:") else None

    def transform(self, go: yaml.UObject) -> yaml.UObject | None:
        for c in R.components(self.doc, go):
            if c.class_id in (R.TRANSFORM, R.RECTTRANSFORM):
                return c
        return None

    def comp(self, go: yaml.UObject, class_id: int) -> yaml.UObject | None:
        for c in R.components(self.doc, go):
            if c.class_id == class_id:
                return c
        return None

    def _transform_id_of(self, key: str | None) -> int:
        """The Transform id that stands for a node in m_Children / m_Father (0 for the scene root)."""
        if key is None:
            return 0
        o = self.obj(key)
        if o is None:
            return 0
        if o.class_id == R.PREFABINSTANCE:
            s = self._stripped_root(o)
            return s.file_id if s is not None else 0
        t = self.transform(o)
        return t.file_id if t is not None else 0

    def _stripped_root(self, pi: yaml.UObject, make: bool = False) -> yaml.UObject | None:
        """The stand-in Transform a scene uses to refer to a placed prefab's root (made if asked and missing)."""
        root = self._root_of(pi)
        for o in self.doc.objects:
            if (o.stripped and o.class_id in (R.TRANSFORM, R.RECTTRANSFORM)
                    and ref(o.get("m_PrefabInstance")) == pi.file_id
                    and ref(o.get("m_CorrespondingSourceObject")) == root.transform_id):
                return o
        if not make:
            return None
        sid = self.new_id()
        self.doc.add(R.TRANSFORM, sid, "Transform", {
            "m_CorrespondingSourceObject": FlowMap(fileID=root.transform_id, guid=self._guid_of(pi), type=3),
            "m_PrefabInstance": FlowMap(fileID=pi.file_id), "m_PrefabAsset": FlowMap(fileID=0)})
        s = self.doc.by_id(sid)
        s.stripped = True
        s.lines[0] += " stripped"
        return s

    @staticmethod
    def _guid_of(pi: yaml.UObject) -> str:
        return R.source_of(pi).get("guid", "")

    def _root_of(self, pi: yaml.UObject) -> R.PrefabRoot:
        return R.prefab_root(self.project, self.project.path_of(self._guid_of(pi)) or "")

    def parent_key(self, key: str) -> str | None:
        o = self.obj(key)
        if o is None:
            return None
        if o.class_id == R.PREFABINSTANCE:
            fid = ref(o.get("m_Modification", "m_TransformParent"))
        else:
            t = self.transform(o)
            fid = ref(t.get("m_Father")) if t is not None else 0
        father = self.doc.by_id(fid) if fid else None
        if father is None:
            return None
        if father.stripped:
            return f"u:{ref(father.get('m_PrefabInstance'))}"
        return f"u:{ref(father.get('m_GameObject'))}"

    def _corrected(self, key: str | None) -> bool:
        o = self.obj(key) if key else None
        if o is None:
            return False
        if o.class_id == R.PREFABINSTANCE:
            return self._root_of(o).corrected
        return o.class_id == R.GAMEOBJECT and R.corrected_go(self.doc, o)

    def new_id(self) -> int:
        while True:
            i = self.rng.randint(100_000_000, 2_147_483_647)
            if self.doc.by_id(i) is None:
                return i

    # ------------------------------------------------------------ structure

    def create(self, fields: dict, parent: str | None) -> str:
        """Make an empty object for a node (a placed prefab or model if the node is one), then apply its fields."""
        placed = fields.get("prefab")
        mesh = fields.get("mesh")
        model = mesh is not None and mesh[0] == "asset" and mesh[1].lower().endswith(R.MODEL_EXTENSIONS)
        if placed is None and model and not fields.get("mesh.name"):
            # A whole model placed as the object itself needs Unity's ids for the model's root, known for
            # ModelImporter files (FBX, OBJ...) but not for glTF. Otherwise the object holds it in a mesh helper.
            simple = not any(k.startswith(("collider", "body.", "camera.", "light.")) for k in fields)
            if simple and not mesh[1].lower().endswith(R.GLTF_EXTENSIONS):
                placed = mesh[1]
        if placed is not None:
            path = self.project.asset_path(placed)
            guid = self.project.guid_of(path) if path else None
            if guid:
                key = self._create_instance(guid, R.prefab_root(self.project, path), parent)
                self.apply(key, fields, fields)
                return key
            self.warnings.append(f"{fields.get('name')}: {placed} isn't in the Unity project; made an empty object")
        go_id, tr_id = self.new_id(), self.new_id()
        go = _template(R.GAMEOBJECT, go_id)
        go.set("m_Component", [{"component": FlowMap(fileID=tr_id)}])
        go.set("m_Name", fields.get("name", "GameObject"))
        go.set("m_TagString", "Untagged")
        go.set("m_Layer", 0)
        go.set("m_StaticEditorFlags", 0)
        go.set("m_IsActive", 1)
        tr = _template(R.TRANSFORM, tr_id)
        tr.set("m_GameObject", FlowMap(fileID=go_id))
        tr.set("m_Children", [])
        tr.set("m_Father", FlowMap(fileID=0))
        for o in (go, tr):
            self.doc.insert(o)
        key = f"u:{go_id}"
        self._attach(key, parent)
        self.apply(key, fields, fields)
        return key

    def _create_instance(self, guid: str, root: R.PrefabRoot, parent: str | None) -> str:
        pi_id = self.new_id()
        target = FlowMap(fileID=root.transform_id, guid=guid, type=3)
        p, q = root.position, root.rotation
        mods = [{"target": target, "propertyPath": prop, "value": yaml.scalar(float(v)), "objectReference": FlowMap(fileID=0)}
                for prop, v in [("m_LocalPosition.x", p[0]), ("m_LocalPosition.y", p[1]), ("m_LocalPosition.z", p[2]),
                                ("m_LocalRotation.w", q[3]), ("m_LocalRotation.x", q[0]), ("m_LocalRotation.y", q[1]),
                                ("m_LocalRotation.z", q[2])]]
        mods.append({"target": FlowMap(fileID=root.go_id, guid=guid, type=3), "propertyPath": "m_Name",
                     "value": root.name, "objectReference": FlowMap(fileID=0)})
        pi = self.doc.add(R.PREFABINSTANCE, pi_id, "PrefabInstance", {
            "m_ObjectHideFlags": 0, "serializedVersion": 2,
            "m_Modification": {"serializedVersion": 3, "m_TransformParent": FlowMap(fileID=0), "m_Modifications": mods,
                               "m_RemovedComponents": [], "m_RemovedGameObjects": [], "m_AddedGameObjects": [],
                               "m_AddedComponents": []},
            "m_SourcePrefab": FlowMap(fileID=100100000, guid=guid, type=3),
        })
        self._stripped_root(pi, make=True)
        key = f"u:{pi_id}"
        self._attach(key, parent)
        return key

    def _attach(self, key: str, parent: str | None) -> None:
        o = self.obj(key)
        tid = self._transform_id_of(key)
        pobj = self.obj(parent) if parent is not None else None
        if pobj is not None and pobj.class_id == R.PREFABINSTANCE:
            ptid = self._stripped_root(pobj, make=True).file_id
        else:
            ptid = self._transform_id_of(parent)
        if o.class_id == R.PREFABINSTANCE:
            o.set(("m_Modification", "m_TransformParent"), FlowMap(fileID=ptid))
        else:
            self.transform(o).set("m_Father", FlowMap(fileID=ptid))
        if parent is None:
            if self.prefab:
                return
            roots = self._scene_roots()
            entry = o.file_id if o.class_id == R.PREFABINSTANCE else tid
            items = list(roots.get("m_Roots", default=[]) or [])
            items.append(FlowMap(fileID=entry))
            roots.set("m_Roots", items)
        elif pobj is not None and pobj.class_id == R.PREFABINSTANCE:
            # an object added to a placed prefab: Unity lists it on the instance
            root = self._root_of(pobj)
            added = list(pobj.get("m_Modification", "m_AddedGameObjects", default=[]) or [])
            added.append({"targetCorrespondingSourceObject": FlowMap(fileID=root.transform_id, guid=self._guid_of(pobj), type=3),
                          "insertIndex": -1, "addedObject": FlowMap(fileID=tid)})
            pobj.set(("m_Modification", "m_AddedGameObjects"), added)
        else:
            pt = self.doc.by_id(ptid)
            kids = list(pt.get("m_Children", default=[]) or [])
            kids.append(FlowMap(fileID=tid))
            pt.set("m_Children", kids)

    def _detach(self, key: str) -> None:
        o = self.obj(key)
        tid = self._transform_id_of(key)
        parent = self.parent_key(key)
        pobj = self.obj(parent) if parent is not None else None
        if parent is None:
            if self.prefab:
                return
            roots = self._scene_roots()
            gone = {tid, o.file_id}
            roots.set("m_Roots", [r for r in (roots.get("m_Roots", default=[]) or []) if ref(r) not in gone])
        elif pobj is not None and pobj.class_id == R.PREFABINSTANCE:
            added = pobj.get("m_Modification", "m_AddedGameObjects", default=[]) or []
            pobj.set(("m_Modification", "m_AddedGameObjects"),
                     [a for a in added if ref(a.get("addedObject")) != tid])
        else:
            pt = self.doc.by_id(self._transform_id_of(parent))
            if pt is not None and not pt.stripped:
                pt.set("m_Children", [c for c in (pt.get("m_Children", default=[]) or []) if ref(c) != tid])

    def _scene_roots(self) -> yaml.UObject:
        roots = self.doc.of_class(R.SCENEROOTS)
        if roots:
            return roots[0]
        return self.doc.add(R.SCENEROOTS, 9223372036854775807, "SceneRoots",
                            {"m_ObjectHideFlags": 0, "m_Roots": []})

    def reparent(self, key: str, parent: str | None) -> None:
        if self.parent_key(key) == parent:
            return
        self._detach(key)
        self._attach(key, parent)

    def _key_for_transform(self, o: yaml.UObject) -> str:
        if o.class_id == R.PREFABINSTANCE:
            return f"u:{o.file_id}"
        if o.stripped:
            return f"u:{ref(o.get('m_PrefabInstance'))}"
        return f"u:{ref(o.get('m_GameObject'))}"

    def children(self, parent: str | None) -> list[str]:
        if parent is None:
            if self.prefab:
                out = [f"u:{ref(o.get('m_GameObject'))}" for o in self.doc.objects
                       if o.class_id in (R.TRANSFORM, R.RECTTRANSFORM) and not o.stripped and ref(o.get("m_Father")) == 0]
                return out + [f"u:{o.file_id}" for o in self.doc.of_class(R.PREFABINSTANCE)
                              if ref(o.get("m_Modification", "m_TransformParent")) == 0]
            out = []
            for r in self._scene_roots().get("m_Roots", default=[]) or []:
                o = self.doc.by_id(ref(r))
                if o is not None:
                    out.append(self._key_for_transform(o))
            return out
        pobj = self.obj(parent)
        if pobj is not None and pobj.class_id == R.PREFABINSTANCE:
            s = self._stripped_root(pobj)
            if s is None:
                return []
            return [self._key_for_transform(o) for o in self.doc.objects
                    if o.class_id in (R.TRANSFORM, R.RECTTRANSFORM, R.PREFABINSTANCE) and not o.stripped
                    and ref(o.get("m_Father") if o.class_id != R.PREFABINSTANCE
                            else o.get("m_Modification", "m_TransformParent")) == s.file_id]
        pt = self.doc.by_id(self._transform_id_of(parent))
        out = []
        for c in (pt.get("m_Children", default=[]) or []) if pt is not None else []:
            o = self.doc.by_id(ref(c))
            if o is not None:
                out.append(self._key_for_transform(o))
        return out

    def order_children(self, parent: str | None, keys: list[str]) -> None:
        """Put these children first, in this order; any others keep their relative order after them."""
        if parent is None and self.prefab:
            return
        pobj = self.obj(parent) if parent is not None else None
        if pobj is not None and pobj.class_id == R.PREFABINSTANCE:
            return  # Unity keeps objects added to a placed prefab in its own order
        current = self.children(parent)
        want = [k for k in keys if k in current] + [k for k in current if k not in keys]
        if want == current:
            return
        if parent is None:
            roots = self._scene_roots()
            entries = []
            for k in want:
                o = self.obj(k)
                entries.append(FlowMap(fileID=o.file_id if o.class_id == R.PREFABINSTANCE else self._transform_id_of(k)))
            roots.set("m_Roots", entries)
        else:
            pt = self.doc.by_id(self._transform_id_of(parent))
            pt.set("m_Children", [FlowMap(fileID=self._transform_id_of(k)) for k in want])

    def delete(self, key: str) -> None:
        o = self.obj(key)
        if o is None:
            return
        for kid in self.children(key):
            self.delete(kid)
        self._detach(key)
        if o.class_id == R.PREFABINSTANCE:
            for s in [x for x in self.doc.objects if ref(x.get("m_PrefabInstance")) == o.file_id]:
                self.doc.remove(s.file_id)
        else:
            for c in R.components(self.doc, o):
                self.doc.remove(c.file_id)
        self.doc.remove(o.file_id)

    # ------------------------------------------------------------ fields

    def apply(self, key: str, changed: dict, full: dict) -> None:
        """Write changed fields. full is the node's complete new field set (for context, e.g. transforms)."""
        o = self.obj(key)
        if o is None:
            self.warnings.append(f"{key}: not in the Unity scene")
            return
        if o.class_id == R.PREFABINSTANCE:
            self._apply_instance(o, changed, full)
            return
        go = o
        if "name" in changed:
            go.set("m_Name", changed["name"])
        if "active" in changed:
            go.set("m_IsActive", 1 if changed["active"] else 0)
        if "parent" in changed:
            self.reparent(key, changed["parent"])
        groups = {k.split(".")[0] for k in changed}
        if groups & {"mesh", "materials"}:
            self._mesh(go, full)
        if "camera" in groups:
            self._camera(go, full)
        if "light" in groups:
            self._light(go, full)
        if "body" in groups:
            self._body(go, full)
        if any(is_collider_group(g) for g in groups):
            self._collider(go, full)
        if groups & {"scripts", "script"}:
            self._scripts(go, full)
        if groups & {"position", "rotation", "scale", "parent", "camera", "light"}:
            self._transform(key, self.transform(go), full)

    def _transform(self, key: str, tr: yaml.UObject, full: dict) -> None:
        if tr is None or "position" not in full:
            return
        p, q, s = coords.to_unity(full["position"], full["rotation"], full["scale"],
                                  corrected=self._corrected(key), parent_corrected=self._corrected(self.parent_key(key)))
        tr.set("m_LocalPosition", _vec(p))
        tr.set("m_LocalRotation", _vec(q, "xyzw"))
        tr.set("m_LocalScale", _vec(s))
        if "m_LocalEulerAnglesHint" in tr.data:
            tr.set("m_LocalEulerAnglesHint", euler_hint(q))

    def _apply_instance(self, pi: yaml.UObject, changed: dict, full: dict) -> None:
        """A placed prefab or model: only where it is and what it's called; the rest comes from the prefab."""
        key = f"u:{pi.file_id}"
        if "parent" in changed:
            self.reparent(key, changed["parent"])
        guid = self._guid_of(pi)
        root = self._root_of(pi)
        if not root.transform_id:  # unknown ids (glTF): reuse the targets of its existing overrides
            mods = pi.get("m_Modification", "m_Modifications", default=[]) or []
            root = R.PrefabRoot(R._target_of(mods, "m_Name"), R._target_of(mods, "m_LocalPosition.x"), root.name)
            if not root.transform_id:
                self.warnings.append(f"{full.get('name')}: can't move this placed glTF model from TwinScene")
                return
        vals = {}
        if "name" in changed:
            vals[(root.go_id, "m_Name")] = changed["name"]
        if "active" in changed:
            vals[(root.go_id, "m_IsActive")] = "1" if changed["active"] else "0"
        if {"position", "rotation", "scale", "parent"} & changed.keys() and "position" in full:
            p, q, s = coords.to_unity(full["position"], full["rotation"], full["scale"], corrected=root.corrected,
                                      parent_corrected=self._corrected(self.parent_key(key)))
            for a, v in zip("xyz", p):
                vals[(root.transform_id, f"m_LocalPosition.{a}")] = yaml.scalar(v)
            for a, v in zip("xyzw", q):
                vals[(root.transform_id, f"m_LocalRotation.{a}")] = yaml.scalar(v)
            for a, v, d in zip("xyz", s, root.scale):
                if abs(v - d) > 1e-6 or self._has_mod(pi, f"m_LocalScale.{a}"):
                    vals[(root.transform_id, f"m_LocalScale.{a}")] = yaml.scalar(v)
        if vals:
            self._set_mods(pi, guid, vals)
        wanted = changed.get("prefab") or (changed.get("mesh") or (None, None))[1]
        wanted_path = self.project.asset_path(wanted) if wanted else None
        if wanted_path and self.project.guid_of(wanted_path) not in (None, guid):
            self.warnings.append(f"{full.get('name')}: swapping which prefab is placed isn't synced yet")
        if any(k.startswith("o:") for k in changed):
            self._apply_overrides(pi, guid, changed, full)
        unsupported = {k.split(".")[0] for k in changed if not k.startswith("o:")} - {
            "name", "active", "parent", "position", "rotation", "scale", "index", "mesh", "prefab"}
        if unsupported:
            self.warnings.append(f"{full.get('name')}: {', '.join(sorted(unsupported))} on a placed prefab isn't synced yet")

    _MANAGED = ("m_LocalPosition.", "m_LocalRotation.", "m_LocalScale.", "m_LocalEulerAnglesHint.", "m_IsActive",
                "m_Color.", "m_Intensity", "m_Range", "m_Materials.Array.data[")

    def _apply_overrides(self, pi: yaml.UObject, guid: str, changed: dict, full: dict) -> None:
        """Edits inside a placed prefab (overrides.py) -> its m_Modifications, aimed at the prefab's own objects."""
        from .. import overrides, scripts as S
        table = getattr(self.project, "scripts", None)
        path = self.project.path_of(guid)
        base = overrides.unity_base(self.project, path) if path else None
        if base is None:
            self.warnings.append(f"{full.get('name')}: prefab {path or guid} missing; its inner changes weren't synced")
            return
        bdoc, bscene = base
        wanted = overrides.of(full)
        inners = {overrides.parse(k)[0] for k in changed if overrides.parse(k)}
        mods = [dict(m) for m in (pi.get("m_Modification", "m_Modifications", default=[]) or [])]
        for inner in inners:
            go = bdoc.by_id(int(inner[2:])) if inner.startswith("u:") else None
            basenode = bscene.nodes.get(inner)
            if go is None or basenode is None:
                self.warnings.append(f"{full.get('name')}: {inner} isn't in the prefab any more")
                continue
            comps = R.components(bdoc, go)
            tr = next((c for c in comps if c.class_id in (R.TRANSFORM, R.RECTTRANSFORM)), None)
            light = next((c for c in comps if c.class_id == R.LIGHT), None)
            rend = next((c for c in comps if c.class_id == R.MESHRENDERER), None)
            mine = {go.file_id} | {c.file_id for c in comps}
            is_root = go.file_id == self._root_of(pi).go_id  # its place, name and visibility are the object's own
            managed = self._MANAGED[5:] if is_root else self._MANAGED
            mods = [m for m in mods if not (ref(m.get("target")) in mine and
                                            str(m.get("propertyPath", "")).startswith(managed))]
            want = wanted.get(inner, {})
            if is_root:
                want = {k: v for k, v in want.items() if k not in overrides.TRANSFORM + ("active",)}

            def add(target, prop, value="", obj=None):
                mods.append({"target": FlowMap(fileID=target.file_id, guid=guid, type=3), "propertyPath": prop,
                             "value": value, "objectReference": obj or FlowMap(fileID=0)})
            if tr is not None and any(f in want for f in overrides.TRANSFORM):
                parent_go = bdoc.by_id(int(basenode.parent[2:])) if basenode.parent else None
                p, q, sc = coords.to_unity(want.get("position", basenode.get("position")),
                                           want.get("rotation", basenode.get("rotation")),
                                           want.get("scale", basenode.get("scale")), corrected=R.corrected_go(bdoc, go),
                                           parent_corrected=parent_go is not None and R.corrected_go(bdoc, parent_go))
                for a, v in zip("xyz", p):
                    add(tr, f"m_LocalPosition.{a}", yaml.scalar(v))
                for a, v in zip("xyzw", q):
                    add(tr, f"m_LocalRotation.{a}", yaml.scalar(v))
                if any(abs(a - b) > 1e-6 for a, b in zip(sc, overrides.unity_inner_transform_raw(bdoc, go)[2])):
                    for a, v in zip("xyz", sc):
                        add(tr, f"m_LocalScale.{a}", yaml.scalar(v))
            if "active" in want:
                add(go, "m_IsActive", "1" if want["active"] else "0")
            if light is not None:
                if "light.color" in want:
                    for a, v in zip("rgb", want["light.color"]):
                        add(light, f"m_Color.{a}", yaml.scalar(float(v)))
                if "light.energy" in want:
                    add(light, "m_Intensity", yaml.scalar(float(want["light.energy"])))
                if "light.range" in want:
                    add(light, "m_Range", yaml.scalar(float(want["light.range"])))
            if rend is not None and "materials" in want:
                base_mats = basenode.get("materials", [])
                for i, key in enumerate(want["materials"]):
                    if i >= len(base_mats) or base_mats[i] != key:
                        add(rend, f"m_Materials.Array.data[{i}]", "", self._material_ref(key))
            for c in comps:  # paired scripts' settings ("axis.x", "axis.y"... for vectors and colours)
                pair = table.by_guid.get((c.get("m_Script") or {}).get("guid", "")) \
                    if table is not None and c.class_id == MONOBEHAVIOUR else None
                if pair is None:
                    continue
                names = {us.name for us, _ in pair.shared.values()}
                mods = [m for m in mods if not (ref(m.get("target")) == c.file_id and
                                                str(m.get("propertyPath", "")).split(".", 1)[0] in names)]
                for k, (us, _) in pair.shared.items():
                    if pair.field_name(k) not in want:
                        continue
                    v = S.to_unity(want[pair.field_name(k)], us.type)
                    for sub, x in (v.items() if isinstance(v, dict) else [(None, v)]):
                        add(c, us.name if sub is None else f"{us.name}.{sub}", yaml.scalar(x))
        pi.set(("m_Modification", "m_Modifications"), mods)

    @staticmethod
    def _has_mod(pi, path: str) -> bool:
        return any(isinstance(m, dict) and m.get("propertyPath") == path
                   for m in pi.get("m_Modification", "m_Modifications", default=[]) or [])

    def _set_mods(self, pi: yaml.UObject, guid: str, vals: dict) -> None:
        mods = [dict(m) for m in (pi.get("m_Modification", "m_Modifications", default=[]) or [])]
        for (target_id, path), value in vals.items():
            hit = next((m for m in mods if m.get("propertyPath") == path and ref(m.get("target")) == target_id), None)
            if hit is not None:
                hit["value"] = value
            else:
                mods.append({"target": FlowMap(fileID=target_id, guid=guid, type=3), "propertyPath": path,
                             "value": value, "objectReference": FlowMap(fileID=0)})
        pi.set(("m_Modification", "m_Modifications"), mods)

    # -- components

    def _add_comp(self, go: yaml.UObject, class_id: int) -> yaml.UObject:
        c = _template(class_id, self.new_id())
        c.set("m_GameObject", FlowMap(fileID=go.file_id))
        self.doc.insert(c)
        comps = list(go.get("m_Component", default=[]) or [])
        comps.append({"component": FlowMap(fileID=c.file_id)})
        go.set("m_Component", comps)
        return c

    def _remove_comp(self, go: yaml.UObject, class_id: int) -> None:
        c = self.comp(go, class_id)
        if c is None:
            return
        go.set("m_Component", [x for x in go.get("m_Component") if ref(x.get("component")) != c.file_id])
        self.doc.remove(c.file_id)

    def _ensure(self, go: yaml.UObject, class_id: int) -> yaml.UObject:
        return self.comp(go, class_id) or self._add_comp(go, class_id)

    def _camera(self, go, f: dict) -> None:
        if "camera.fov" not in f:
            self._remove_comp(go, R.CAMERA)
            return
        c = self._ensure(go, R.CAMERA)
        c.set("field of view", float(f["camera.fov"]))
        c.set("near clip plane", float(f.get("camera.near", 0.3)))
        c.set("far clip plane", float(f.get("camera.far", 1000)))
        c.set("orthographic", 1 if f.get("camera.ortho") else 0)
        if f.get("camera.ortho"):
            c.set("orthographic size", float(f.get("camera.ortho_size", 5)))

    def _light(self, go, f: dict) -> None:
        if "light.type" not in f:
            self._remove_comp(go, R.LIGHT)
            return
        c = self._ensure(go, R.LIGHT)
        c.set("m_Type", LIGHT_CODES[f["light.type"]])
        r, g, b = f.get("light.color", (1, 1, 1))
        c.set("m_Color", FlowMap(r=float(r), g=float(g), b=float(b), a=1.0))
        c.set("m_Intensity", float(f.get("light.energy", 1)))
        if "light.range" in f:
            c.set("m_Range", float(f["light.range"]))
        if "light.spot_angle" in f:
            c.set("m_SpotAngle", float(f["light.spot_angle"]))
        shadows = int(yaml.num(c.get("m_Shadows", "m_Type"), 0))
        if bool(f.get("light.shadows")) != (shadows != 0):
            c.set(("m_Shadows", "m_Type"), 2 if f.get("light.shadows") else 0)

    def _body(self, go, f: dict) -> None:
        t = f.get("body.type")
        if t not in ("rigid", "kinematic"):
            self._remove_comp(go, R.RIGIDBODY)
            return
        c = self._ensure(go, R.RIGIDBODY)
        c.set("m_Mass", float(f.get("body.mass", 1)))
        c.set("m_IsKinematic", 1 if t == "kinematic" else 0)

    def _collider(self, go, f: dict) -> None:
        all_wanted = colliders(f)
        wanted = [w for w in all_wanted if w["shape"] != "cylinder"]
        classes = set(COLLIDER_CLASS.values())
        existing = [c for c in R.components(self.doc, go) if c.class_id in classes]
        if [c.class_id for c in existing] != [COLLIDER_CLASS[w["shape"]] for w in wanted]:
            for c in existing:  # the set of colliders changed: rebuild them in order
                self._remove_comp_obj(go, c)
            existing = [self._add_comp(go, COLLIDER_CLASS[w["shape"]]) for w in wanted]
        corrected = R.corrected_go(self.doc, go)
        for c, w in zip(existing, wanted):
            center = w.get("center", (0, 0, 0))
            if corrected:
                center = coords.qrot(coords.HALF_TURN_Y, center)
            c.set("m_Center", _vec(coords.clean(coords.mirror_pos(center))))
            c.set("m_IsTrigger", 1 if w.get("trigger") else 0)
            if w["shape"] == "box":
                c.set("m_Size", _vec(w.get("size", (1, 1, 1))))
            else:
                c.set("m_Radius", float(w.get("radius", 0.5)))
            if w["shape"] == "capsule":
                c.set("m_Height", float(w.get("height", 2)))
                c.set("m_Direction", 1)
        # Unity has no cylinder collider: a convex mesh collider with Unity's cylinder mesh, scaled, in a helper
        keep = set()
        for i, w in enumerate(all_wanted):
            if w["shape"] != "cylinder":
                continue
            kind = f"cylinder{i + 1}"
            part = self._part(go, kind) or self._make_part(go, kind)
            keep.add(part.file_id)
            tr = self.transform(part)
            center = w.get("center", (0, 0, 0))
            if corrected:
                center = coords.qrot(coords.HALF_TURN_Y, center)
            tr.set("m_LocalPosition", _vec(coords.clean(coords.mirror_pos(center))))
            r, h = float(w.get("radius", 0.5)), float(w.get("height", 2))
            tr.set("m_LocalScale", _vec((2 * r, h / 2, 2 * r)))
            mc = self.comp(part, R.MESHCOLLIDER) or self._add_mesh_collider(part)
            mc.set("m_IsTrigger", 1 if w.get("trigger") else 0)
        for kid in self.children(f"u:{go.file_id}"):
            o = self.obj(kid)
            k = part_kind(self.doc, o) if o is not None and o.class_id == R.GAMEOBJECT else None
            if k and k.startswith("cylinder") and o.file_id not in keep:
                self.delete(kid)

    def _scripts(self, go, f: dict) -> None:
        """Paired scripts (scripts.py): add the wanted ones, remove paired ones no longer wanted, set the shared
        settings. Scripts without a Godot partner are never touched."""
        from .. import scripts as S
        table = getattr(self.project, "scripts", None)
        if table is None:
            return
        wanted = list(f.get("scripts") or ())
        have: dict[str, yaml.UObject] = {}
        for c in R.components(self.doc, go):
            if c.class_id != MONOBEHAVIOUR:
                continue
            pair = table.by_guid.get((c.get("m_Script") or {}).get("guid", ""))
            if pair is None:
                continue
            if pair.name in wanted and pair.name not in have:
                have[pair.name] = c
            else:
                self._remove_comp_obj(go, c)
        for name in wanted:
            pair = table.pairs.get(name)
            if pair is None:
                self.warnings.append(f"{go.get('m_Name')}: script {name} isn't paired any more")
                continue
            c = have.get(name)
            if c is None:
                if not pair.guid:
                    self.warnings.append(f"{go.get('m_Name')}: {pair.unity.path} has no .meta yet (open the project in "
                                         "Unity once); the script wasn't added")
                    continue
                c = self._add_script(go, pair.guid)
            for k, (us, _) in pair.shared.items():
                v = f.get(pair.field_name(k))
                if v is not None:
                    c.set(us.name, S.to_unity(v, us.type))

    def _add_script(self, go, guid: str) -> yaml.UObject:
        mb_id = self.new_id()
        c = self.doc.add(MONOBEHAVIOUR, mb_id, "MonoBehaviour", {
            "m_ObjectHideFlags": 0, "m_CorrespondingSourceObject": FlowMap(fileID=0),
            "m_PrefabInstance": FlowMap(fileID=0), "m_PrefabAsset": FlowMap(fileID=0),
            "m_GameObject": FlowMap(fileID=go.file_id), "m_Enabled": 1, "m_EditorHideFlags": 0,
            "m_Script": FlowMap(fileID=11500000, guid=guid, type=3), "m_Name": "", "m_EditorClassIdentifier": ""})
        comps = list(go.get("m_Component"))
        comps.append({"component": FlowMap(fileID=mb_id)})
        go.set("m_Component", comps)
        return c

    def _add_mesh_collider(self, go) -> yaml.UObject:
        cid = self.new_id()
        c = self.doc.add(R.MESHCOLLIDER, cid, "MeshCollider", {
            "m_ObjectHideFlags": 0, "m_CorrespondingSourceObject": FlowMap(fileID=0),
            "m_PrefabInstance": FlowMap(fileID=0), "m_PrefabAsset": FlowMap(fileID=0),
            "m_GameObject": FlowMap(fileID=go.file_id), "m_Material": FlowMap(fileID=0), "m_IsTrigger": 0,
            "m_Enabled": 1, "serializedVersion": 5, "m_Convex": 1, "m_CookingOptions": 30,
            "m_Mesh": FlowMap(fileID=R.BUILTIN_CYLINDER, guid=R.BUILTIN_EXTRA, type=0)})
        comps = list(go.get("m_Component"))
        comps.append({"component": FlowMap(fileID=cid)})
        go.set("m_Component", comps)
        return c

    def _remove_comp_obj(self, go, c) -> None:
        go.set("m_Component", [x for x in go.get("m_Component") if ref(x.get("component")) != c.file_id])
        self.doc.remove(c.file_id)

    def _mesh(self, go, f: dict) -> None:
        mesh = f.get("mesh")
        part = self._part(go, "mesh")
        if mesh is None:
            for target in [go] + ([part] if part is not None else []):
                self._remove_comp(target, R.MESHFILTER)
                self._remove_comp(target, R.MESHRENDERER)
            return
        if mesh[0] != "primitive" and f.get("mesh.name"):  # one mesh out of a model file
            path = self.project.asset_path(mesh[1])
            guid = self.project.guid_of(path) if path else None
            names, _ = models.unity_model_info(self.project.path, path) if guid else ({}, 1.0)
            fid = next((k for k, v in names.items() if v == f["mesh.name"]), None)
            if fid is not None:
                if part is not None:
                    self._remove_comp(part, R.MESHFILTER)
                    self._remove_comp(part, R.MESHRENDERER)
                mf = self._ensure(go, R.MESHFILTER)
                mf.set("m_Mesh", FlowMap(fileID=fid, guid=guid, type=3))
                self._materials(go, f)
                return
            self.warnings.append(f"{f.get('name')}: mesh {f['mesh.name']} of {mesh[1]} isn't listed in its Unity .meta")
        if mesh[0] != "primitive" and mesh[1].lower().endswith(R.MODEL_EXTENSIONS):
            self._model_helper(go, mesh[1], f)
            return
        if mesh[0] != "primitive":
            mf = self.comp(go, R.MESHFILTER)
            if mf is None:
                where = mesh[1].rsplit("/", 1)[-1]
                self.warnings.append(f"{f.get('name')}: its mesh ({where}) is a Godot mesh resource Unity can't read; "
                                     "export it as glTF, FBX or OBJ to sync it")
            self._materials(go, f)
            return
        prim = mesh[1]
        size = f.get("mesh.size", R.PRIMITIVE_SIZE[prim])
        unit = all(abs(a - b) < 1e-6 for a, b in zip(size, R.PRIMITIVE_SIZE[prim]))
        holder = go if unit and part is None else (part or self._make_part(go, "mesh"))
        if holder is not go:  # the mesh lives on a helper child; make sure the object itself has none
            self._remove_comp(go, R.MESHFILTER)
            self._remove_comp(go, R.MESHRENDERER)
            ptr = self.transform(holder)
            ptr.set("m_LocalScale", _vec(unit_primitive_scale(prim, size)))
        mf = self._ensure(holder, R.MESHFILTER)
        mf.set("m_Mesh", FlowMap(fileID=PRIMITIVE_IDS[prim], guid=R.BUILTIN_EXTRA, type=0))
        self._materials(holder, f)

    def _model_helper(self, go, model_ref: str, f: dict) -> None:
        """A whole model file placed inside a mesh helper (no overrides, so Unity's ids for it aren't needed)."""
        path = self.project.asset_path(model_ref)
        guid = self.project.guid_of(path) if path else None
        if guid is None:
            self.warnings.append(f"{f.get('name')}: model {model_ref} isn't in the Unity project")
            return
        gltf = path.lower().endswith(R.GLTF_EXTENSIONS)
        if gltf and not self._has_gltfast():
            self.warnings.append(f"{f.get('name')}: Unity needs the glTFast package (com.unity.cloud.gltfast) to show "
                                 ".glb/.gltf models; add it in the Package Manager")
        for c in (R.MESHFILTER, R.MESHRENDERER):
            self._remove_comp(go, c)
        part = self._part(go, "mesh") or self._make_part(go, "mesh")
        for kid in self.children(f"u:{part.file_id}"):
            o = self.obj(kid)
            if o is not None and o.class_id == R.PREFABINSTANCE and self._guid_of(o) == guid:
                return
            self.delete(kid)
        if R.corrected_go(self.doc, go):  # the object carries the camera/light half turn; keep the model upright
            self.transform(part).set("m_LocalRotation", _vec(coords.HALF_TURN_Y, "xyzw"))
        pi_id = self.new_id()
        self.doc.add(R.PREFABINSTANCE, pi_id, "PrefabInstance", {
            "m_ObjectHideFlags": 0, "serializedVersion": 2,
            "m_Modification": {"serializedVersion": 3, "m_TransformParent": FlowMap(fileID=self.transform(part).file_id),
                               "m_Modifications": [], "m_RemovedComponents": [], "m_RemovedGameObjects": [],
                               "m_AddedGameObjects": [], "m_AddedComponents": []},
            "m_SourcePrefab": FlowMap(fileID=R.GLTFAST_PREFAB_ID if gltf else R.MODEL_PREFAB_ID, guid=guid, type=3),
        })

    def _has_gltfast(self) -> bool:
        try:
            return "gltfast" in (self.project.path / "Packages" / "manifest.json").read_text(encoding="utf-8").lower()
        except OSError:
            return False

    def _materials(self, holder, f: dict) -> None:
        mr = self._ensure(holder, R.MESHRENDERER)
        refs = [self._material_ref(m) for m in f.get("materials", [])] or [DEFAULT_MATERIAL]
        current = mr.get("m_Materials", default=[]) or []
        if [(ref(a), (a or {}).get("guid")) for a in current] != [(ref(b), b.get("guid")) for b in refs]:
            mr.set("m_Materials", refs)

    def _material_ref(self, key: str) -> FlowMap:
        if key.startswith("unity:builtin:"):
            return FlowMap(fileID=int(key.rsplit(":", 1)[1]), guid=R.BUILTIN_DEFAULT, type=0)
        path = self.project.asset_path(key)
        guid = self.project.guid_of(path) if path else None
        if guid is None:
            self.warnings.append(f"material {key} isn't in the Unity project; using the default")
            return DEFAULT_MATERIAL
        return FlowMap(fileID=2100000, guid=guid, type=2)

    def _part(self, go, kind: str) -> yaml.UObject | None:
        for kid in self.children(f"u:{go.file_id}"):
            o = self.obj(kid)
            if o is not None and o.class_id == R.GAMEOBJECT and part_kind(self.doc, o) == kind:
                return o
        return None

    def _make_part(self, go, kind: str) -> yaml.UObject:
        key = self.create({"name": f"{go.get('m_Name')} ({kind})", "position": (0, 0, 0),
                           "rotation": coords.IDENTITY, "scale": (1, 1, 1)}, f"u:{go.file_id}")
        part = self.obj(key)
        mb_id = self.new_id()
        self.doc.add(MONOBEHAVIOUR, mb_id, "MonoBehaviour", {
            "m_ObjectHideFlags": 0, "m_CorrespondingSourceObject": FlowMap(fileID=0),
            "m_PrefabInstance": FlowMap(fileID=0), "m_PrefabAsset": FlowMap(fileID=0),
            "m_GameObject": FlowMap(fileID=part.file_id), "m_Enabled": 1, "m_EditorHideFlags": 0,
            "m_Script": FlowMap(fileID=11500000, guid=PART_SCRIPT_GUID, type=3), "m_Name": "",
            "m_EditorClassIdentifier": "TwinScene.Runtime::TwinScene.TwinScenePart", "part": kind})
        comps = list(part.get("m_Component"))
        comps.append({"component": FlowMap(fileID=mb_id)})
        part.set("m_Component", comps)
        return part



# ---------------------------------------------------------------- materials and scenes

MATERIAL_TEMPLATE = Path(__file__).with_name("material.mat")
EMPTY_SCENE = Path(__file__).with_name("empty_scene.unity")
URP_LIT = FlowMap(fileID=4800000, guid="933532a4fcc9baf4fa0491de14d08ed7", type=3)
MAT_META = """fileFormatVersion: 2
guid: {guid}
NativeFormatImporter:
  externalObjects: {{}}
  mainObjectFileID: 2100000
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def uses_urp(project: UnityProject) -> bool:
    manifest = project.path / "Packages" / "manifest.json"
    try:
        return "com.unity.render-pipelines.universal" in manifest.read_text(encoding="utf-8")
    except OSError:
        return False


def _set_list_prop(mat: yaml.UObject, kind: str, name: str, value) -> None:
    items = [dict(i) for i in (mat.get("m_SavedProperties", kind, default=[]) or [])]
    for it in items:
        if name in it:
            if yaml._same(it[name], value):
                return
            it[name] = value
            break
    else:
        items.append({name: value})
    mat.set(("m_SavedProperties", kind), items)


def patch_material(mat: yaml.UObject, fields: dict, urp: bool) -> None:
    """Neutral material fields -> a Unity Material object (Standard or URP Lit names)."""
    if "name" in fields:
        mat.set("m_Name", fields["name"])
    if "albedo" in fields:
        r, g, b, a = (list(fields["albedo"]) + [1.0])[:4]
        _set_list_prop(mat, "m_Colors", "_BaseColor" if urp else "_Color",
                       FlowMap(r=float(r), g=float(g), b=float(b), a=float(a)))
    if "metallic" in fields:
        _set_list_prop(mat, "m_Floats", "_Metallic", float(fields["metallic"]))
    if "roughness" in fields:
        _set_list_prop(mat, "m_Floats", "_Smoothness" if urp else "_Glossiness", round(1 - float(fields["roughness"]), 6))


def write_material(project: UnityProject, asset_path: str, fields: dict, rng: random.Random | None = None) -> str:
    """Create a .mat (and its .meta) at asset_path. Returns the new guid."""
    rng = rng or random.Random()
    doc = yaml.load(MATERIAL_TEMPLATE)
    mat = next(o for o in doc.objects if o.class_id == R.MATERIAL)
    urp = uses_urp(project)
    if urp:
        mat.set("m_Shader", URP_LIT)
    patch_material(mat, dict(fields, name=fields.get("name") or Path(asset_path).stem), urp)
    target = project.path / asset_path
    make_folders(project, target.parent, rng)
    yaml.save(doc, target)
    guid = "%032x" % rng.getrandbits(128)
    (target.parent / (target.name + ".meta")).write_text(MAT_META.format(guid=guid), encoding="utf-8", newline="\n")
    project.register(asset_path, guid)
    return guid


FOLDER_META = """fileFormatVersion: 2
guid: {guid}
folderAsset: yes
DefaultImporter:
  externalObjects: {{}}
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""
SCENE_META = """fileFormatVersion: 2
guid: {guid}
DefaultImporter:
  externalObjects: {{}}
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def make_folders(project: UnityProject, folder: Path, rng: random.Random) -> None:
    """Create folders inside Assets/ with their .meta files, as Unity would."""
    assets = project.path / "Assets"
    missing = []
    f = folder
    while f != assets and assets in f.parents and not f.exists():
        missing.append(f)
        f = f.parent
    for f in reversed(missing):
        f.mkdir()
        meta = f.with_name(f.name + ".meta")
        if not meta.exists():
            meta.write_text(FOLDER_META.format(guid="%032x" % rng.getrandbits(128)), encoding="utf-8", newline="\n")
    folder.mkdir(parents=True, exist_ok=True)


def write_scene_meta(scene_path: Path, rng: random.Random) -> None:
    meta = scene_path.with_name(scene_path.name + ".meta")
    if not meta.exists():
        meta.write_text(SCENE_META.format(guid="%032x" % rng.getrandbits(128)), encoding="utf-8", newline="\n")


def update_material(project: UnityProject, asset_path: str, fields: dict) -> None:
    path = project.path / asset_path
    doc = yaml.load(path)
    mat = next(o for o in doc.objects if o.class_id == R.MATERIAL)
    patch_material(mat, fields, uses_urp(project))
    yaml.save(doc, path)


def new_scene() -> yaml.Document:
    return yaml.load(EMPTY_SCENE)


PREFAB_META = """fileFormatVersion: 2
guid: {guid}
PrefabImporter:
  externalObjects: {{}}
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def new_prefab() -> yaml.Document:
    return yaml.loads("%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n")


def write_prefab_meta(path: Path, rng: random.Random) -> str:
    """A new .prefab's .meta (scenes refer to the prefab by this guid). Returns the guid."""
    meta = path.with_name(path.name + ".meta")
    if meta.exists():
        m = re.search(r"^guid:\s*([0-9a-f]{32})", meta.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    guid = "%032x" % rng.getrandbits(128)
    meta.write_text(PREFAB_META.format(guid=guid), encoding="utf-8", newline="\n")
    return guid
