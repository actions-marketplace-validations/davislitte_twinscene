"""Edits a Godot .tscn from TwinScene fields: creates, changes and deletes nodes and the resources they use.

Godot often needs several nodes where Unity has one object with several components (a rigid body and its collision
shape, say). The extra nodes are "helpers": they carry metadata/twinscene_part and the reader folds them back into
their owner. Existing nodes are patched, never rebuilt, so anything TwinScene doesn't understand stays as it was.
"""

from __future__ import annotations

import random
import re
import string
from pathlib import Path

from .. import coords
from .. import models
from ..model import collider_prefix, colliders, is_collider_group
from ..project import GodotProject
from . import tscn
from .reader import BODIES, DEFAULTS, LIGHTS, PART_META, PRIMITIVES, SCENE_FILES, instance_default, script_pairs_on
from .variant import Call

LIGHT_TYPES = {v: k for k, v in LIGHTS.items()}
BODY_TYPES = {"static": "StaticBody3D", "rigid": "RigidBody3D", "kinematic": "AnimatableBody3D"}
MESH_TYPES = {v: k for k, v in PRIMITIVES.items()}
SHAPE_TYPES = {"box": "BoxShape3D", "sphere": "SphereShape3D", "capsule": "CapsuleShape3D",
               "cylinder": "CylinderShape3D"}
SCENE_MODELS = (".fbx", ".gltf", ".glb", ".blend", ".dae")  # Godot imports these as scenes; .obj as a mesh
_BAD_NAME = re.compile(r'[.:@/"%]')


def _corrected(f: dict) -> bool:
    return "camera.fov" in f or f.get("light.type") in ("directional", "spot")


def _transform(pos, rot, scale) -> Call | None:
    if (all(abs(c) < 1e-7 for c in pos) and coords.qnorm(rot) == coords.IDENTITY
            and all(abs(c - 1) < 1e-7 for c in scale)):
        return None
    rows = coords.basis_from(coords.qnorm(rot), scale)
    vals = [c for r in rows for c in r] + list(pos)
    return Call("Transform3D", tuple(0.0 if abs(v) < 1e-7 else float(v) for v in vals))


def _f(*v) -> tuple:
    return tuple(float(c) for c in v)


class GodotEditor:
    def __init__(self, doc: tscn.Document, project: GodotProject, scene_path: str, rng: random.Random | None = None,
                 prefab: bool = False):
        """prefab=True: the scene is used like a Unity prefab, so its root node is an object too (not the scene)."""
        self.doc = doc
        self.project = project
        self.scene_path = scene_path
        self.rng = rng or random.Random()
        self.prefab = prefab
        self.warnings: list[str] = []
        self.parent: dict[int, tscn.Section | None] = {}
        self.kids: dict[int, list[tscn.Section]] = {}
        self.by_key: dict[str, tscn.Section] = {}
        self.root: tscn.Section | None = None
        self.touched_subs: set[str] = set()
        # Sections for nodes that come from a placed scene (Godot's edits inside instances). They aren't TwinScene
        # objects, but they must survive, and follow their instance if it's renamed or moved.
        self.inside: dict[int, str] = {}  # section -> its parent path below the nearest node we know
        self.fresh_root = False
        self.root_used = False
        self.extract_requests: list[tuple[str, str]] = []  # (model res path, mesh name) to save as their own files
        by_path: dict[str, tscn.Section] = {}
        for sec in doc.nodes():
            self.kids[id(sec)] = []
            pa = sec.get("parent")
            if pa is None:
                self.root = sec
                self.parent[id(sec)] = None
                by_path["."] = sec
                if prefab:
                    self.by_key[self._key(sec)] = sec
                continue
            name = sec.get("name", "")
            path = name if pa == "." else f"{pa}/{name}"
            p = by_path.get(pa)
            if p is None:  # under a node that only exists inside a placed scene
                parts = pa.split("/")
                for i in range(len(parts) - 1, 0, -1):
                    p = by_path.get("/".join(parts[:i]))
                    if p is not None:
                        self.inside[id(sec)] = "/".join(parts[i:])
                        break
                if p is None:
                    self.warnings.append(f"{path}: parent {pa!r} not found; left as it is")
                    continue
            elif sec.get("type") is None and sec.get("instance") is None:
                self.inside[id(sec)] = ""  # an edit to a node of the placed scene `p`
            else:
                by_path[path] = sec
                if sec.get("unique_id") is not None:
                    self.by_key[f"g:{sec.get('unique_id')}"] = sec
            self.parent[id(sec)] = p
            self.kids[id(p)].append(sec)
        self.editables: dict[int, tscn.Section] = {}  # [editable path=...] section -> the instance it names
        self.editable_needed: list[tscn.Section] = []
        for ed in doc.by_tag("editable"):
            target = by_path.get(ed.get("path"))
            if target is not None:
                self.editables[id(ed)] = target
        if self.root is None:
            self.root = tscn.new_section("node", {"name": Path(scene_path).stem or "Scene", "type": "Node3D",
                                                  "unique_id": self._new_uid()})
            self.doc.insert(self.root)
            self.parent[id(self.root)] = None
            self.kids[id(self.root)] = []
            self.fresh_root = True
            if prefab:
                self.by_key[self._key(self.root)] = self.root

    @staticmethod
    def _key(sec: tscn.Section) -> str:
        return f"g:{sec.get('unique_id')}" if sec.get("unique_id") is not None else "g:path:."

    # ------------------------------------------------------------ tree

    def sec(self, key: str | None) -> tscn.Section:
        return self.root if key is None else self.by_key[key]

    def key_of(self, sec: tscn.Section) -> str | None:
        if sec is self.root:
            return self._key(sec) if self.prefab else None
        return f"g:{sec.get('unique_id')}"

    def is_part(self, sec: tscn.Section) -> bool:
        return sec.value(PART_META) is not None

    def _own(self, sec: tscn.Section) -> bool:
        """A node of this scene that TwinScene syncs (not a helper, not something inside a placed scene)."""
        return id(sec) not in self.inside and not self.is_part(sec)

    def children(self, key: str | None) -> list[str]:
        if key is None and self.prefab:
            return [self._key(self.root)]
        return [self.key_of(s) for s in self.kids[id(self.sec(key))] if self._own(s)]

    def parent_key(self, key: str) -> str | None:
        p = self.parent.get(id(self.sec(key)))
        if p is None or (p is self.root and not self.prefab):
            return None
        return self.key_of(p)

    def _new_uid(self) -> int:
        used = {s.get("unique_id") for s in self.doc.nodes()}
        while True:
            u = self.rng.randint(1, 2_147_483_647)
            if u not in used:
                return u

    def _name_for(self, wanted: str, parent: tscn.Section | None, me: tscn.Section | None) -> str:
        base = _BAD_NAME.sub("_", wanted).strip() or "Node"
        if parent is None:
            return base
        taken = {s.get("name") for s in self.kids[id(parent)] if s is not me}
        name, n = base, 2
        while name in taken:
            name, n = f"{base}{n}", n + 1
        return name

    def _add_node(self, name: str, typ: str | None, parent: tscn.Section, extra: dict | None = None) -> tscn.Section:
        attrs = {"name": self._name_for(name, parent, None)}
        if typ:
            attrs["type"] = typ
        attrs["parent"] = "."
        attrs.update(extra or {})
        attrs["unique_id"] = self._new_uid()
        sec = tscn.new_section("node", attrs)
        self.doc.insert(sec)  # position is fixed up in finish()
        self.parent[id(sec)] = parent
        self.kids[id(sec)] = []
        self.kids[id(parent)].append(sec)
        self.by_key[f"g:{attrs['unique_id']}"] = sec
        return sec

    def _remove_node(self, sec: tscn.Section) -> None:
        for k in list(self.kids[id(sec)]):
            self._remove_node(k)
        p = self.parent.get(id(sec))
        if p is not None:
            self.kids[id(p)].remove(sec)
        self.by_key.pop(self.key_of(sec) or "", None)
        for prop in sec.props:
            self._forget(prop.value)
        self.doc.remove(sec)

    def reparent(self, key: str, parent: str | None) -> None:
        sec, newp = self.sec(key), self.sec(parent)
        old = self.parent[id(sec)]
        if old is newp or sec is self.root:
            return
        self.kids[id(old)].remove(sec)
        self.kids[id(newp)].append(sec)
        self.parent[id(sec)] = newp
        sec.set_attr("name", self._name_for(sec.get("name"), newp, sec))

    def order_children(self, parent: str | None, keys: list[str]) -> None:
        if parent is None and self.prefab:
            return
        p = self.sec(parent)
        cur = self.kids[id(p)]
        want_secs = [self.by_key[k] for k in keys if k in self.by_key and self.by_key[k] in cur]
        rest = [s for s in cur if s not in want_secs]
        fixed = [s for s in rest if not self._own(s)]  # helpers and placed-scene edits stay first
        others = [s for s in rest if self._own(s)]
        self.kids[id(p)] = fixed + want_secs + others

    def delete(self, key: str) -> None:
        if key in self.by_key and self.by_key[key] is not self.root:
            self._remove_node(self.by_key[key])

    def finish(self) -> None:
        """Write names, parent paths and node order back into the document; drop resources nothing uses."""
        order: list[tscn.Section] = []
        paths: dict[int, str] = {}

        def walk(sec, path):
            order.append(sec)
            paths[id(sec)] = path
            for k in self.kids[id(sec)]:
                tail = self.inside.get(id(k))
                if tail:
                    k.set_attr("parent", tail if path == "." else f"{path}/{tail}")
                    kpath = None  # (only placed-scene edits hang below these, handled the same way)
                else:
                    k.set_attr("parent", path)
                    kpath = k.get("name") if path == "." else f"{path}/{k.get('name')}"
                walk(k, kpath if kpath is not None else f"{k.get('parent')}/{k.get('name')}")

        walk(self.root, ".")
        for ed in self.doc.by_tag("editable"):  # instances may have been renamed or moved
            target = self.editables.get(id(ed))
            if target is not None and id(target) in paths and target in order:
                ed.set_attr("path", paths[id(target)])
            elif target is not None and target not in order:  # its instance is gone
                self.doc.remove(ed)
        named = {id(t) for t in self.editables.values()}
        for inst in self.editable_needed:
            if id(inst) not in named and inst in order:
                ed = tscn.new_section("editable", {"path": paths[id(inst)]})
                self.doc.insert(ed)
                self.editables[id(ed)] = inst
                named.add(id(inst))
        nodes = [s for s in self.doc.sections if s.tag == "node"]
        if nodes != order:
            first = self.doc.sections.index(nodes[0]) if nodes else len(self.doc.sections)
            rest = [s for s in self.doc.sections if s.tag != "node"]
            before = [s for s in rest if self.doc.sections.index(s) < first]
            after = [s for s in rest if s not in before]
            # Anything the tree doesn't hold (shouldn't happen) is kept at the end rather than lost.
            stray = [s for s in nodes if s not in order]
            self.doc.sections = before + order + stray + after
        for sid in list(self.touched_subs):
            if not self._used(sid):
                sub = self.doc.sub_resource(sid)
                if sub is not None:
                    self.doc.sections.remove(sub)
        self._tidy_blank_lines()
        head = self.doc.header
        if head is not None and head.get("load_steps") is not None:
            head.set_attr("load_steps", len(self.doc.by_tag("ext_resource")) + len(self.doc.by_tag("sub_resource")) + 1)

    def _tidy_blank_lines(self) -> None:
        secs = self.doc.sections
        for i, s in enumerate(secs):
            if i < len(secs) - 1:
                if not any(not t.strip() for t in s.trailer):
                    s.trailer.append("")
            else:
                while s.trailer and not s.trailer[-1].strip():
                    s.trailer.pop()

    # ------------------------------------------------------------ resources

    def _rid(self, prefix: str) -> str:
        used = {s.get("id") for s in self.doc.sections}
        while True:
            rid = prefix + "".join(self.rng.choice(string.ascii_lowercase + string.digits) for _ in range(5))
            if rid not in used:
                return rid

    def _sub(self, typ: str, props: dict) -> Call:
        rid = self._rid(f"{typ}_")
        sec = tscn.new_section("sub_resource", {"type": typ, "id": rid}, props)
        subs = self.doc.by_tag("sub_resource")
        if subs:
            self.doc.insert(sec, after=subs[-1])
        else:
            first = next((s for s in self.doc.sections if s.tag == "node"), None)
            self.doc.insert(sec, before=first) if first is not None else self.doc.insert(sec)
        self.touched_subs.add(rid)
        return Call("SubResource", (rid,))

    def _ext(self, typ: str, path: str) -> Call:
        for s in self.doc.by_tag("ext_resource"):
            if s.get("path") == path:
                return Call("ExtResource", (s.get("id"),))
        exts = self.doc.by_tag("ext_resource")
        rid = self._rid(f"{len(exts) + 1}_")
        sec = tscn.new_section("ext_resource", {"type": typ, "path": path, "id": rid})
        if exts:
            self.doc.insert(sec, after=exts[-1])
        else:
            head = self.doc.header
            self.doc.insert(sec, after=head) if head is not None else self.doc.insert(sec, before=self.doc.sections[0])
        return Call("ExtResource", (rid,))

    def _refs(self, v, out: list) -> list:
        if isinstance(v, Call):
            if v.name == "SubResource" and v.args:
                out.append(v.args[0])
            for a in v.args:
                self._refs(a, out)
        elif isinstance(v, (list, tuple)):
            for a in v:
                self._refs(a, out)
        elif isinstance(v, dict):
            for a in v.values():
                self._refs(a, out)
        return out

    def _used(self, rid: str) -> bool:
        for s in self.doc.sections:
            if s.tag == "sub_resource" and s.get("id") == rid:
                continue
            for p in s.props:
                if rid in self._refs(p.value, []):
                    return True
        return False

    def _forget(self, v) -> None:
        for rid in self._refs(v, []):
            self.touched_subs.add(rid)

    def _own_sub(self, holder: tscn.Section, prop: str, typ: str) -> tscn.Section:
        """The sub-resource in holder[prop], made private to holder (copied if shared) and of type typ."""
        cur = self.doc.resolve(holder.value(prop))
        if cur is not None and cur.tag == "sub_resource" and cur.get("type") == typ:
            uses = sum(self._refs(p.value, []).count(cur.get("id")) for s in self.doc.sections for p in s.props)
            if uses <= 1:
                return cur
            copy = {p.key: p.value for p in cur.props if p.key}
            ref = self._sub(typ, copy)
        else:
            self._forget(holder.value(prop))
            ref = self._sub(typ, {})
        holder.set(prop, ref)
        return self.doc.resolve(ref)

    @staticmethod
    def _put(sec: tscn.Section, key: str, value, default=None) -> None:
        """Set a property, or remove it when it's Godot's default (Godot leaves defaults out of the file)."""
        from .variant import same
        if default is not None and same(value, default):
            sec.remove(key)
        else:
            sec.set(key, value)

    # ------------------------------------------------------------ create

    def create(self, fields: dict, parent: str | None) -> str:
        if parent is None and self.prefab:
            if self.fresh_root and not self.root_used:  # a new prefab scene: its root becomes this object
                self.root_used = True
                if fields.get("prefab"):
                    self.warnings.append(f"{fields.get('name')}: a prefab variant (inherited scene) isn't synced yet")
                self.root.set_attr("type", self._main_type(fields, self.root))
                key = self._key(self.root)
                self.apply(key, fields, fields)
                return key
            self.warnings.append(f"{fields.get('name')}: a prefab has one root; added under it instead")
            parent = self._key(self.root)
        p = self.sec(parent)
        mesh = fields.get("mesh")
        name = fields.get("name", "Node")
        placed = self.project.res_path(fields["prefab"]) if fields.get("prefab") else None
        only_mesh = not any(k.startswith(("collider", "body.", "camera.", "light.")) for k in fields) and \
            not fields.get("mesh.name")
        if placed is None and mesh and mesh[0] == "asset" and mesh[1].lower().endswith(SCENE_MODELS) and only_mesh:
            placed = self.project.res_path(mesh[1])
        if fields.get("prefab") and placed is None:
            self.warnings.append(f"{name}: {fields['prefab']} isn't in the Godot project; made an empty node")
        if placed:
            sec = self._add_node(name, None, p, {"instance": self._ext("PackedScene", placed)})
            key = self.key_of(sec)
            self.apply(key, fields, fields)
            return key
        typ = self._main_type(fields, p)
        sec = self._add_node(name, typ, p)
        key = self.key_of(sec)
        self.apply(key, fields, fields)
        return key

    def _main_type(self, f: dict, parent: tscn.Section) -> str:
        if f.get("body.type") in BODY_TYPES:
            return BODY_TYPES[f["body.type"]]
        if "camera.fov" in f:
            return "Camera3D"
        if f.get("light.type") in LIGHT_TYPES:
            return LIGHT_TYPES[f["light.type"]]
        cols = colliders(f)
        if "mesh" in f:
            mesh = f["mesh"]
            whole_model = mesh[0] == "asset" and mesh[1].lower().endswith(SCENE_MODELS) and not f.get("mesh.name")
            scaled = abs(float(f.get("mesh.import_scale", 1.0)) - 1) > 1e-6 or any(f.get("mesh.offset", ()))
            if (whole_model or scaled) and cols:
                return self._collider_type(cols, parent)  # the mesh goes in a helper
            if scaled:
                return "Node3D"
            if whole_model:
                return "Node3D"
            return "MeshInstance3D"
        if cols:
            return self._collider_type(cols, parent)
        return "Node3D"

    def _collider_type(self, cols: list, parent: tscn.Section) -> str:
        if len(cols) == 1 and parent.get("type") in BODIES and not cols[0].get("trigger") and \
                all(abs(c) < 1e-7 for c in cols[0].get("center", (0, 0, 0))):
            return "CollisionShape3D"  # a plain shape of its parent's body
        if all(c.get("trigger") for c in cols):
            return "Area3D"
        return "Node3D" if self._in_body(parent) else "StaticBody3D"

    def _in_body(self, sec: tscn.Section | None) -> bool:
        while sec is not None and sec is not self.root:
            if sec.get("type") in BODIES:
                return True
            sec = self.parent.get(id(sec))
        return False

    # ------------------------------------------------------------ apply

    def apply(self, key: str, changed: dict, full: dict) -> None:
        sec = self.by_key.get(key)
        if sec is None:
            self.warnings.append(f"{key}: not in the Godot scene")
            return
        if "name" in changed:
            sec.set_attr("name", self._name_for(changed["name"], self.parent[id(sec)], sec))
        if "parent" in changed and sec is not self.root:
            self.reparent(key, changed["parent"])
        if "active" in changed:
            self._put(sec, "visible", bool(changed["active"]), True)
        if {"position", "rotation", "scale"} & changed.keys() and "position" in full:
            t = _transform(full["position"], full["rotation"], full["scale"])
            placed = self.doc.resolve(sec.get("instance")) if sec.get("instance") is not None else None
            if t is None and placed is not None and instance_default(self.project, placed.get("path", ""))[0:2] !=                     ((0.0, 0.0, 0.0), coords.IDENTITY):
                t = Call("Transform3D", (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0))
            if t is None:
                sec.remove("transform")
            else:
                sec.set("transform", t)
        if sec.get("instance") is not None:
            # A placed scene: its mesh, lights, physics and so on come from the scene it places.
            if any(k.startswith("o:") for k in changed):
                self._apply_overrides(sec, changed, full)
            other = {k.split(".")[0] for k in changed if not k.startswith("o:")} - {
                "name", "parent", "active", "position", "rotation", "scale", "index", "prefab", "mesh"}
            placed = self.doc.resolve(sec.get("instance"))
            wanted = self.project.res_path(changed["prefab"]) if changed.get("prefab") else None
            if wanted and placed is not None and placed.get("path") != wanted:
                self.warnings.append(f"{sec.get('name')}: swapping which scene is placed isn't synced yet")
            if "materials" in other and full.get("mesh"):
                other.discard("materials")
                self.warnings.append(f"{sec.get('name')}: materials on a model mesh (FBX/glTF) aren't synced yet; "
                                     "Godot shows the model's own")
            if other:
                self.warnings.append(f"{sec.get('name')}: {', '.join(sorted(other))} on a placed scene isn't synced yet")
            return
        groups = {k.split(".")[0] for k in changed}
        col = any(is_collider_group(g) for g in groups)
        if "body" in groups or (col and "body.type" not in full):
            self._body(sec, full)
        if groups & {"mesh", "materials"}:
            self._mesh(sec, full)
        if "camera" in groups:
            self._camera(sec, full)
        if "light" in groups:
            self._light(sec, full)
        if col or "camera" in groups or "light" in groups:
            self._collider(sec, full)
        if groups & {"scripts", "script"}:
            self._scripts(sec, full)

    def _scripts(self, sec: tscn.Section, f: dict) -> None:
        """Paired scripts (scripts.py). The first one goes on the node itself when the node's type can hold it
        (Godot only attaches a script to the class it extends, or a subclass); others go on helper child nodes.
        Scripts without a Unity partner are never touched."""
        from .. import scripts as S
        table = getattr(self.project, "scripts", None)
        if table is None:
            return
        wanted = list(f.get("scripts") or ())
        res = self.doc.resolve(sec.value("script")) if sec.value("script") is not None else None
        main = table.by_res.get(res.get("path", "")) if res is not None else None
        free = sec.value("script") is None
        if main is not None and main.name not in wanted:
            self._clear_script(sec, main)
            main, free = None, True
        helpers = {str(k.value(PART_META))[len("script:"):]: k for k in self.kids[id(sec)]
                   if str(k.value(PART_META) or "").startswith("script:")}
        placed: dict[str, tscn.Section] = {}
        for name in wanted:
            pair = table.pairs.get(name)
            if pair is None:
                self.warnings.append(f"{sec.get('name')}: script {name} isn't paired any more")
                continue
            if main is not None and main.name == name:
                placed[name] = sec
                continue
            base = pair.godot.base
            fits = S.godot_is_a(sec.get("type"), base)
            if free and fits is not False and name not in helpers:
                sec.set("script", self._ext("Script", pair.godot.path))
                main, free = pair, False
                placed[name] = sec
                continue
            h = helpers.get(name)
            if h is None:
                if S.godot_is_a(base, "CollisionObject3D") or S.godot_is_a(base, "VisualInstance3D"):
                    self.warnings.append(f"{sec.get('name')}: {pair.godot.path.rsplit('/', 1)[-1]} extends {base}, "
                                         f"which this {sec.get('type') or 'node'} can't hold; change the node's type "
                                         "in Godot and TwinScene will keep the script there")
                    continue
                htype = base if S.godot_is_a(base, "Node") else "Node"
                h = self._add_node(f"{sec.get('name')} ({name})", htype, sec)
                h.set(PART_META, f"script:{name}")
                h.set("script", self._ext("Script", pair.godot.path))
            placed[name] = h
        for name, h in helpers.items():
            if name not in placed:
                self._remove_node(h)
        for name, target in placed.items():
            pair = table.pairs[name]
            for k, (_, gs) in pair.shared.items():
                v = f.get(pair.field_name(k))
                if v is not None:
                    default = S.to_godot(gs.default, gs.type) if gs.default is not None else None
                    self._put(target, gs.name, S.to_godot(v, gs.type), default)

    def _clear_script(self, sec: tscn.Section, pair) -> None:
        sec.remove("script")
        for gs in pair.godot.settings.values():
            sec.remove(gs.name)

    def _apply_overrides(self, sec: tscn.Section, changed: dict, full: dict) -> None:
        """Edits inside a placed scene (overrides.py) -> sections for its inner nodes (and the instance's own
        properties), plus the [editable] marker Godot needs for them."""
        from .. import overrides, scripts as S
        placed = self.doc.resolve(sec.get("instance"))
        base = overrides.godot_base(self.project, placed.get("path", "")) if placed is not None else None
        if base is None:
            self.warnings.append(f"{sec.get('name')}: its scene is missing; inner changes weren't synced")
            return
        bdoc, bscene, paths = base
        wanted = overrides.of(full)
        for inner in {overrides.parse(k)[0] for k in changed if overrides.parse(k)}:
            rel, basenode = paths.get(inner), bscene.nodes.get(inner)
            if rel is None or basenode is None:
                self.warnings.append(f"{sec.get('name')}: {inner} isn't in {placed.get('path')} any more")
                continue
            want = wanted.get(inner, {})
            target = sec if rel == "." else self._inner_section(sec, rel, inner, create=bool(want))
            if target is None:
                continue
            if target is not sec:
                if any(f in want for f in overrides.TRANSFORM):
                    t = _transform(want.get("position", basenode.get("position")),
                                   want.get("rotation", basenode.get("rotation")),
                                   want.get("scale", basenode.get("scale")))
                    target.set("transform", t if t is not None else Call(
                        "Transform3D", (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)))
                else:
                    target.remove("transform")
                if "active" in want:
                    target.set("visible", bool(want["active"]))
                else:
                    target.remove("visible")
            for field, prop in (("light.color", "light_color"), ("light.energy", "light_energy")):
                if field in want:
                    v = want[field]
                    target.set(prop, Call("Color", _f(*v, 1.0)) if field == "light.color" else float(v))
                else:
                    target.remove(prop)
            range_prop = "spot_range" if basenode.get("light.type") == "spot" else "omni_range"
            if "light.range" in want:
                target.set(range_prop, float(want["light.range"]))
            else:
                target.remove(range_prop)
            base_mats = basenode.get("materials", [])
            for prop in [p.key for p in target.props if p.key.startswith("surface_material_override/")]:
                target.remove(prop)
            for i, key in enumerate(want.get("materials", [])):
                if i >= len(base_mats) or base_mats[i] != key:
                    ref = self._material_ref(key)
                    if ref is not None:
                        target.set(f"surface_material_override/{i}", ref)
            for pair in script_pairs_on(self.project, bdoc, rel, basenode):  # paired scripts' settings
                for k, (_, gs) in pair.shared.items():
                    if pair.field_name(k) in want:
                        target.set(gs.name, S.to_godot(want[pair.field_name(k)], gs.type))
                    else:
                        target.remove(gs.name)
            if target is not sec and not any(p.key for p in target.props):  # nothing left to change
                self._remove_node(target)

    def _inner_section(self, inst: tscn.Section, rel: str, inner: str, create: bool):
        """The section that edits the node at `rel` inside a placed scene (made if asked)."""
        parent_rel, _, name = rel.rpartition("/")
        for k in self.kids[id(inst)]:
            if id(k) in self.inside and self.inside[id(k)] == parent_rel and k.get("name") == name \
                    and k.get("type") is None and k.get("instance") is None:
                return k
        if not create:
            return None
        attrs = {"name": name, "parent": "."}
        if inner.startswith("g:") and inner[2:].isdigit():
            attrs["unique_id"] = int(inner[2:])
        sec = tscn.new_section("node", attrs)
        self.doc.insert(sec)
        self.parent[id(sec)] = inst
        self.kids[id(sec)] = []
        self.kids[id(inst)].append(sec)
        self.inside[id(sec)] = parent_rel
        self.editable_needed.append(inst)  # Godot only reads edits to an instance's insides with this marker
        return sec

    def _part(self, sec: tscn.Section, kind: str, typ: str | None, full: dict, inside: tscn.Section | None = None):
        """Find (or with typ, make) the helper node carrying `kind` for sec."""
        holder = inside or sec
        for k in self.kids[id(holder)]:
            if k.value(PART_META) == kind:
                if typ and k.get("type") != typ:
                    k.set_attr("type", typ)
                return k
        if is_collider_group(kind) and inside is None:  # it may be inside a static or area helper
            for box in ("static", "area"):
                b = self._part(sec, box, None, full)
                found = self._part(sec, kind, None, full, inside=b) if b is not None else None
                if found is not None:
                    return found
        if typ is None:
            return None
        part = self._add_node(f"{sec.get('name')} ({kind})", typ, holder)
        part.set(PART_META, kind)
        if kind == "mesh" and _corrected(full):  # the owner carries the camera/light half turn; undo it for the mesh
            part.set("transform", _transform((0, 0, 0), coords.HALF_TURN_Y, (1, 1, 1)))
        return part

    def _slot(self, sec, kind: str, types: tuple[str, ...], make: str | None, full: dict):
        if sec.get("type") in types:
            return sec
        return self._part(sec, kind, make, full)

    def _drop_part(self, sec, kind: str, full: dict) -> None:
        p = self._part(sec, kind, None, full)
        if p is not None:
            self._remove_node(p)

    def _mesh(self, sec, f: dict) -> None:
        mesh = f.get("mesh")
        if sec.get("instance") is not None:
            if mesh is None or mesh[0] != "asset":
                self.warnings.append(f"{sec.get('name')}: replacing a placed model isn't synced yet")
            return
        if mesh is None:
            if sec.get("type") == "MeshInstance3D":
                self._forget(sec.value("mesh"))
                sec.remove("mesh")
            self._drop_part(sec, "mesh", f)
            return
        if mesh[0] == "asset" and f.get("mesh.name") and self.project.res_path(mesh[1]):
            self._model_mesh(sec, self.project.res_path(mesh[1]), f)
            return
        if mesh[0] == "asset" and mesh[1].lower().endswith(SCENE_MODELS) and sec.get("type") != "MeshInstance3D":
            res_path = self.project.res_path(mesh[1])
            if res_path is None:
                self.warnings.append(f"{sec.get('name')}: mesh {mesh[1]} isn't in the Godot project")
                return
            self._model_part(sec, res_path, f)
            if f.get("materials"):
                self.warnings.append(f"{sec.get('name')}: materials on a model mesh (FBX/glTF) aren't synced yet; "
                                     "Godot shows the model's own")
            return
        holder = self._slot(sec, "mesh", ("MeshInstance3D",), "MeshInstance3D", f)
        if mesh[0] == "primitive":
            mtype = MESH_TYPES[mesh[1]]
            res = self._own_sub(holder, "mesh", mtype)
            size = f.get("mesh.size")
            d = DEFAULTS[mtype]
            if size is not None:
                if mesh[1] == "box":
                    self._put(res, "size", Call("Vector3", _f(*size)), d["size"])
                elif mesh[1] in ("plane", "quad"):
                    self._put(res, "size", Call("Vector2", _f(*size[:2])), d["size"])
                elif mesh[1] == "sphere":
                    self._put(res, "radius", float(size[0]), d["radius"])
                    self._put(res, "height", float(size[1]), d["height"])
                elif mesh[1] == "capsule":
                    self._put(res, "radius", float(size[0]), d["radius"])
                    self._put(res, "height", float(size[1]), d["height"])
                else:
                    self._put(res, "top_radius", float(size[0]), d["top_radius"])
                    self._put(res, "bottom_radius", float(size[0]), d["bottom_radius"])
                    self._put(res, "height", float(size[1]), d["height"])
        else:
            res_path = self.project.res_path(mesh[1])
            if res_path is None:
                self.warnings.append(f"{sec.get('name')}: mesh {mesh[1]} isn't in the Godot project")
            elif res_path.lower().endswith(".obj"):
                cur = self.doc.resolve(holder.value("mesh"))
                if cur is None or cur.get("path") != res_path:
                    self._forget(holder.value("mesh"))
                    holder.set("mesh", self._ext("ArrayMesh", res_path))
            else:
                self._model_part(sec, res_path, f)
                if f.get("materials"):
                    self.warnings.append(f"{sec.get('name')}: materials on a model mesh (FBX/glTF) aren't synced yet; "
                                         "Godot shows the model's own")
                return
        self._materials(holder, f)

    def _model_mesh(self, sec, model_res: str, f: dict) -> None:
        """One mesh out of a model file: Godot saves it as its own resource (see models.py), shown by a
        MeshInstance3D, so it gets its own materials like any mesh."""
        name = f["mesh.name"]
        target = models.extracted_path(self.project.path, model_res, name)
        self.extract_requests.append((model_res, name))
        scale = float(f.get("mesh.import_scale", 1.0))
        offset = tuple(f.get("mesh.offset", (0.0, 0.0, 0.0)))
        old = self._part(sec, "mesh", None, f)
        if sec.get("type") == "MeshInstance3D" and abs(scale - 1) < 1e-6 and not any(offset):
            holder = sec
            if old is not None:
                self._remove_node(old)
        else:
            if old is not None and (old.get("instance") is not None or old.get("type") != "MeshInstance3D"):
                self._remove_node(old)
            if sec.get("type") == "MeshInstance3D":  # it held the mesh itself until now: the helper takes over
                self._forget(sec.value("mesh"))
                for prop in [p.key for p in sec.props if p.key == "mesh" or
                             (p.key or "").startswith("surface_material_override/")]:
                    sec.remove(prop)
            holder = self._part(sec, "mesh", "MeshInstance3D", f)
            rot = coords.HALF_TURN_Y if _corrected(f) else coords.IDENTITY
            t = _transform(coords.qrot(rot, offset), rot, (scale, scale, scale))
            if t is None:
                holder.remove("transform")
            else:
                holder.set("transform", t)
        cur = self.doc.resolve(holder.value("mesh"))
        if cur is None or cur.get("path") != target:
            self._forget(holder.value("mesh"))
            holder.set("mesh", self._ext("ArrayMesh", target))
        self._materials(holder, f)

    def _model_part(self, sec, res_path: str, f: dict) -> None:
        """A model file (FBX, glTF...) placed as a helper under the node, which keeps its own type and parts."""
        part = self._part(sec, "mesh", None, f)
        if part is not None:
            placed = self.doc.resolve(part.get("instance")) if part.get("instance") is not None else None
            if placed is not None and placed.get("path") == res_path:
                return
            self._remove_node(part)
        part = self._add_node(f"{sec.get('name')} (mesh)", None, sec, {"instance": self._ext("PackedScene", res_path)})
        part.set(PART_META, "mesh")
        if _corrected(f):
            part.set("transform", _transform((0, 0, 0), coords.HALF_TURN_Y, (1, 1, 1)))

    def _materials(self, holder, f: dict) -> None:
        mats = [m for m in f.get("materials", []) if m]
        refs = [self._material_ref(m) for m in mats]
        refs = [r for r in refs if r is not None]
        mesh = self.doc.resolve(holder.value("mesh"))
        on_mesh = mesh is not None and mesh.tag == "sub_resource" and mesh.value("material") is not None
        if len(refs) == 1 and on_mesh and holder.value("surface_material_override/0") is None:
            if mesh.value("material") != refs[0]:
                self._forget(mesh.value("material"))
                mesh.set("material", refs[0])
            return
        for i, r in enumerate(refs):
            holder.set(f"surface_material_override/{i}", r)
        for p in [p for p in holder.props if p.key.startswith("surface_material_override/")]:
            if int(p.key.rsplit("/", 1)[1]) >= len(refs):
                holder.remove(p.key)

    def _material_ref(self, key: str):
        if key.startswith("godot:") and "#" in key:
            path, rid = key[6:].split("#", 1)
            if path == self.scene_path and self.doc.sub_resource(rid) is not None:
                return Call("SubResource", (rid,))
            self.warnings.append(f"material {key} belongs to another scene")
            return None
        res = self.project.res_path(key)
        if res is None:
            self.warnings.append(f"material {key} isn't in the Godot project")
            return None
        return self._ext("StandardMaterial3D" if res.endswith(".tres") else "Material", res)

    def _camera(self, sec, f: dict) -> None:
        if "camera.fov" not in f:
            if sec.get("type") == "Camera3D":
                self.warnings.append(f"{sec.get('name')}: removing the camera from a camera node isn't synced")
            self._drop_part(sec, "camera", f)
            return
        c = self._slot(sec, "camera", ("Camera3D",), "Camera3D", f)
        d = DEFAULTS["Camera3D"]
        self._put(c, "fov", float(f["camera.fov"]), d["fov"])
        self._put(c, "near", float(f.get("camera.near", 0.05)), d["near"])
        self._put(c, "far", float(f.get("camera.far", 4000)), d["far"])
        self._put(c, "projection", 1 if f.get("camera.ortho") else 0, d["projection"])
        if f.get("camera.ortho"):
            self._put(c, "size", float(f.get("camera.ortho_size", 0.5)) * 2, d["size"])

    def _light(self, sec, f: dict) -> None:
        lt = f.get("light.type")
        light_types = tuple(LIGHTS)
        if lt is None:
            if sec.get("type") in light_types:
                self.warnings.append(f"{sec.get('name')}: removing the light from a light node isn't synced")
            self._drop_part(sec, "light", f)
            return
        want = LIGHT_TYPES[lt]
        if sec.get("type") in light_types:
            c = sec
            if c.get("type") != want:
                c.set_attr("type", want)
        else:
            c = self._part(sec, "light", want, f)
        for k in ("omni_range", "spot_range", "spot_angle"):
            if (k == "omni_range" and want != "OmniLight3D") or (k != "omni_range" and want != "SpotLight3D"):
                c.remove(k)
        d = DEFAULTS["Light3D"]
        r, g, b = f.get("light.color", (1, 1, 1))
        self._put(c, "light_color", Call("Color", _f(r, g, b, 1)), d["light_color"])
        self._put(c, "light_energy", float(f.get("light.energy", 1)), d["light_energy"])
        self._put(c, "shadow_enabled", bool(f.get("light.shadows")), d["shadow_enabled"])
        if want == "OmniLight3D" and "light.range" in f:
            self._put(c, "omni_range", float(f["light.range"]), DEFAULTS["OmniLight3D"]["omni_range"])
        if want == "SpotLight3D":
            if "light.range" in f:
                self._put(c, "spot_range", float(f["light.range"]), DEFAULTS["SpotLight3D"]["spot_range"])
            if "light.spot_angle" in f:
                self._put(c, "spot_angle", float(f["light.spot_angle"]) / 2, DEFAULTS["SpotLight3D"]["spot_angle"])

    def _body(self, sec, f: dict) -> None:
        bt = f.get("body.type")
        typ = sec.get("type")
        cols = colliders(f)
        if bt is None and any(not c.get("trigger") for c in cols) and not self._in_body(self.parent.get(id(sec))):
            bt = "static"  # Unity colliders without a Rigidbody are static
        if typ in BODIES:
            if bt is None:
                if typ == "StaticBody3D":
                    sec.set_attr("type", "Node3D")
                else:
                    self.warnings.append(f"{sec.get('name')}: removing a physics body isn't synced yet")
                return
            if typ != BODY_TYPES[bt] and not (bt == "kinematic" and typ == "CharacterBody3D"):
                sec.set_attr("type", BODY_TYPES[bt])
            if sec.get("type") == "RigidBody3D":
                self._put(sec, "mass", float(f.get("body.mass", 1)), DEFAULTS["RigidBody3D"]["mass"])
            else:
                sec.remove("mass")
            return
        if bt is None:
            return
        if typ in (None, "Node3D") and sec.get("instance") is None:
            sec.set_attr("type", BODY_TYPES[bt])
            if bt == "rigid":
                self._put(sec, "mass", float(f.get("body.mass", 1)), DEFAULTS["RigidBody3D"]["mass"])
        elif bt == "static":
            pass  # _collider puts the shape inside a static helper
        else:
            self.warnings.append(f"{sec.get('name')}: a {typ} can't also be a physics body in Godot; not synced")

    def _collider(self, sec, f: dict) -> None:
        cols = colliders(f)
        if sec.get("type") == "CollisionShape3D":  # the node itself is the shape
            if not cols:
                self._forget(sec.value("shape"))
                sec.remove("shape")
            else:
                self._shape(sec, cols[0])
                if len(cols) > 1:
                    self.warnings.append(f"{sec.get('name')}: a CollisionShape3D holds one shape; extra colliders not synced")
            return
        keep = set()
        for i, c in enumerate(cols):
            kind = collider_prefix(i)
            box = self._shape_box(sec, f, bool(c.get("trigger")))
            holder = self._part(sec, kind, None, f)
            if holder is not None and self.parent[id(holder)] is not box:  # solid <-> trigger: move it
                self._remove_node(holder)
                holder = None
            if holder is None:
                holder = self._part(sec, kind, "CollisionShape3D", f, inside=box)
            # Boxes, spheres and upright capsules look the same turned half way round, so only the offset matters.
            t = _transform(c.get("center", (0, 0, 0)), coords.IDENTITY, (1, 1, 1))
            if t is None:
                holder.remove("transform")
            else:
                holder.set("transform", t)
            self._shape(holder, c)
            keep.add(id(holder))
        for box_kind in (None, "static", "area"):  # drop shapes (and empty boxes) no longer wanted
            box = sec if box_kind is None else self._part(sec, box_kind, None, f)
            if box is None:
                continue
            for k in list(self.kids[id(box)]):
                if is_collider_group(str(k.value(PART_META) or "")) and id(k) not in keep:
                    self._remove_node(k)
            if box_kind is not None and not any(id(k) in keep for k in self.kids[id(box)]):
                self._remove_node(box)

    def _shape_box(self, sec, f: dict, trigger: bool):
        """Where a shape goes: on the node if it's a body (solid) or an Area3D (trigger), else in a helper."""
        typ = sec.get("type")
        if trigger:
            return sec if typ == "Area3D" else self._part(sec, "area", "Area3D", f)
        if typ in BODIES:
            return sec
        if self._in_body(self.parent.get(id(sec))):
            return sec  # (a shape here won't collide in Godot unless the node is the body's direct child)
        return self._part(sec, "static", "StaticBody3D", f)

    def _shape(self, holder, c: dict) -> None:
        cur = self.doc.resolve(holder.value("shape"))
        if cur is not None and cur.get("type") == "WorldBoundaryShape3D" and c["shape"] == "box" and                 c.get("size", (0, 0, 0))[0] >= 9999:
            return  # Unity's stand-in for Godot's endless plane: keep the plane
        st = SHAPE_TYPES[c["shape"]]
        res = self._own_sub(holder, "shape", st)
        d = DEFAULTS[st]
        if c["shape"] == "box":
            self._put(res, "size", Call("Vector3", _f(*c.get("size", (1, 1, 1)))), d["size"])
        else:
            self._put(res, "radius", float(c.get("radius", 0.5)), d["radius"])
        if c["shape"] in ("capsule", "cylinder"):
            self._put(res, "height", float(c.get("height", 2)), d["height"])


def write_material(path, fields: dict) -> None:
    """A standalone Godot material (.tres) from neutral material fields."""
    doc = tscn.loads('[gd_resource type="StandardMaterial3D" format=3]\n\n[resource]\n')
    res = doc.by_tag("resource")[0]
    if fields.get("name"):
        res.set("resource_name", fields["name"])
    a = fields.get("albedo", (1, 1, 1, 1))
    GodotEditor._put(res, "albedo_color", Call("Color", _f(*a)), DEFAULTS["StandardMaterial3D"]["albedo_color"])
    GodotEditor._put(res, "metallic", float(fields.get("metallic", 0)), 0.0)
    GodotEditor._put(res, "roughness", float(fields.get("roughness", 1)), 1.0)
    tscn.save(doc, path)


def update_material(doc: tscn.Document, section: tscn.Section, fields: dict) -> None:
    """Neutral material fields -> an existing StandardMaterial3D section (a .tres [resource] or a sub_resource)."""
    d = DEFAULTS["StandardMaterial3D"]
    if "name" in fields and fields["name"]:
        section.set("resource_name", fields["name"])
    if "albedo" in fields:
        GodotEditor._put(section, "albedo_color", Call("Color", _f(*fields["albedo"])), d["albedo_color"])
    if "metallic" in fields:
        GodotEditor._put(section, "metallic", float(fields["metallic"]), d["metallic"])
    if "roughness" in fields:
        GodotEditor._put(section, "roughness", float(fields["roughness"]), d["roughness"])


def new_scene() -> tscn.Document:
    """An empty scene file; GodotEditor gives it a root node (named after the file)."""
    return tscn.loads("[gd_scene format=3]\n")
