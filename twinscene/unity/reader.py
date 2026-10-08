"""A Unity scene (text YAML) -> TwinScene's neutral model, converting Unity's axes on the way (see coords.py)."""

from __future__ import annotations

from dataclasses import dataclass

from .. import coords, models
from ..model import Material, Node, Scene, free_collider_prefix
from ..project import UnityProject
from . import yaml
from .yaml import num, ref, vec

# Unity class ids (https://docs.unity3d.com/Manual/ClassIDReference.html)
GAMEOBJECT, TRANSFORM, CAMERA, MATERIAL, MESHRENDERER, MESHFILTER = 1, 4, 20, 21, 23, 33
RIGIDBODY, MESHCOLLIDER, BOXCOLLIDER, LIGHT, SPHERECOLLIDER, CAPSULECOLLIDER = 54, 64, 65, 108, 135, 136
BUILTIN_CYLINDER = 10206
RECTTRANSFORM, MONOBEHAVIOUR, PREFABINSTANCE, SCENEROOTS = 224, 114, 1001, 1660057539
PART_SCRIPT_GUID = "c3b83873ca5d4ceba3b6a8287d160837"  # addons/unity/Runtime/TwinScenePart.cs

BUILTIN_EXTRA = "0000000000000000e000000000000000"  # Unity's built-in meshes (Cube, Sphere, ...)
BUILTIN_DEFAULT = "0000000000000000f000000000000000"  # built-in materials and the like
BUILTIN_MESHES = {10202: "box", 10207: "sphere", 10208: "capsule", 10206: "cylinder", 10209: "plane", 10210: "quad"}
# Unity's built-in meshes in Godot's terms: box/plane/quad size, (radius, height) otherwise.
PRIMITIVE_SIZE = {"box": (1.0, 1.0, 1.0), "sphere": (0.5, 1.0), "capsule": (0.5, 2.0), "cylinder": (0.5, 2.0),
                  "plane": (10.0, 10.0), "quad": (1.0, 1.0)}
LIGHT_TYPES = {0: "spot", 1: "directional", 2: "point"}
MODEL_EXTENSIONS = (".fbx", ".obj", ".gltf", ".glb", ".dae", ".blend", ".3ds", ".max")
GLTF_EXTENSIONS = (".gltf", ".glb")  # imported by the glTFast package, not Unity's ModelImporter
MODEL_PREFAB_ID = 100100000  # the placeable prefab inside a model file: ModelImporter ...
GLTFAST_PREFAB_ID = -4161369568681901532  # ... and glTFast (the same for every file; measured)


def instances(doc: yaml.Document) -> list[yaml.UObject]:
    """Placed prefabs. Files saved before Unity 2018.3 use class 1001 "Prefab" both for the prefab asset's own
    marker (m_IsPrefabParent: 1, not a placed copy) and for placed copies (m_ParentPrefab instead of m_SourcePrefab)."""
    return [o for o in doc.of_class(PREFABINSTANCE)
            if o.type_name == "PrefabInstance" or (o.type_name == "Prefab" and str(o.get("m_IsPrefabParent")) != "1")]


def source_of(pi: yaml.UObject) -> dict:
    return pi.get("m_SourcePrefab") or pi.get("m_ParentPrefab") or {}


def corrected_go(doc: yaml.Document, go: yaml.UObject) -> bool:
    """Does this GameObject get the camera/light half turn? (cameras, directional and spot lights)"""
    for comp in components(doc, go):
        if comp.class_id == CAMERA:
            return True
        if comp.class_id == LIGHT and int(num(comp.get("m_Type"), 2)) in (0, 1):
            return True
    return False


def part_kind(doc: yaml.Document, go: yaml.UObject) -> str | None:
    """'mesh' etc. if this GameObject is a TwinScene helper (has the TwinScenePart component)."""
    for c in components(doc, go):
        if c.class_id == MONOBEHAVIOUR and (c.get("m_Script") or {}).get("guid") == PART_SCRIPT_GUID:
            return str(c.get("part", default="mesh"))
    return None


def size_from_part_scale(prim: str, s) -> tuple:
    """The inverse of writer.unit_primitive_scale: a helper's scale -> Godot-style primitive size."""
    if prim == "box":
        return tuple(s)
    if prim == "sphere":
        return (s[0] / 2, s[1])
    if prim in ("capsule", "cylinder"):
        return (s[0] / 2, s[1] * 2)
    if prim == "plane":
        return (s[0] * 10, s[2] * 10)
    return (s[0], s[1])


def components(doc: yaml.Document, go: yaml.UObject) -> list[yaml.UObject]:
    out = []
    for item in go.get("m_Component", default=[]) or []:
        c = doc.by_id(ref(item.get("component") if isinstance(item, dict) else None))
        if c is not None:
            out.append(c)
    return out


def read(doc: yaml.Document, project: UnityProject | None = None) -> Scene:
    project = project or UnityProject(".")
    scene = Scene()
    index = {o.file_id: o for o in doc.objects}
    placed = instances(doc)
    gos = [o for o in doc.objects if o.class_id == GAMEOBJECT and not o.stripped]
    # Objects hidden from the Hierarchy (HideFlags.HideInHierarchy) are editor internals, like the Recorder's
    # settings objects; leave them, and anything under them, alone.
    hidden = {o.file_id for o in gos if int(num(o.get("m_ObjectHideFlags"), 0)) & 1}
    # UI (anything with a RectTransform) isn't synced: Godot builds UI from Control nodes, a different world.
    ui = {ref(o.get("m_GameObject")) for o in doc.objects if o.class_id == RECTTRANSFORM and not o.stripped}
    if ui:
        scene.warnings.append("UI (Canvas and RectTransform objects) isn't synced")
    hidden |= ui
    transform_of: dict[int, yaml.UObject] = {}
    for o in doc.objects:
        if o.class_id in (TRANSFORM, RECTTRANSFORM) and not o.stripped:
            transform_of[ref(o.get("m_GameObject"))] = o

    # Transform file id -> node key, including stripped Transforms that stand for a prefab instance's root.
    node_of_transform: dict[int, str] = {}
    for go in gos:
        t = transform_of.get(go.file_id)
        if t is not None:
            node_of_transform[t.file_id] = f"u:{go.file_id}"
    for o in doc.objects:
        if o.stripped and o.class_id in (TRANSFORM, RECTTRANSFORM):
            pi = ref(o.get("m_PrefabInstance"))
            if pi:
                node_of_transform[o.file_id] = f"u:{pi}"

    # Sibling order: SceneRoots (Unity 2022.2+) for roots, m_Children for everything else.
    order: dict[str, int] = {}
    roots = doc.of_class(SCENEROOTS)
    if roots:
        for i, r in enumerate(roots[0].get("m_Roots", default=[]) or []):
            fid = ref(r)
            key = node_of_transform.get(fid) or (f"u:{fid}" if fid in index and index[fid] in placed else None)
            if key:
                order[key] = i
    for t in transform_of.values():
        for i, c in enumerate(t.get("m_Children", default=[]) or []):
            k = node_of_transform.get(ref(c))
            if k:
                order[k] = i

    corrected = {f"u:{go.file_id}": corrected_go(doc, go) for go in gos}
    for pi in placed:  # a placed camera/light prefab turns its added children too
        corrected[f"u:{pi.file_id}"] = prefab_root(project, project.path_of(source_of(pi).get("guid", "")) or "").corrected

    if hidden:  # their children too
        father = {g.file_id: ref(transform_of[g.file_id].get("m_Father")) for g in gos if g.file_id in transform_of}
        go_of_tr = {t.file_id: g for g, t in transform_of.items()}
        for g in gos:
            f = father.get(g.file_id, 0)
            while f and g.file_id not in hidden:
                if go_of_tr.get(f) in hidden:
                    hidden.add(g.file_id)
                f = father.get(go_of_tr.get(f), 0)
        gos = [g for g in gos if g.file_id not in hidden]
    parts = []
    unpaired: dict[str, int] = {}  # scripts with no Godot partner -> how many objects use them
    for go in gos:
        key = f"u:{go.file_id}"
        t = transform_of.get(go.file_id)
        parent = node_of_transform.get(ref(t.get("m_Father"))) if t is not None else None
        if part_kind(doc, go) is not None and parent is not None:
            parts.append((go, t, parent))
            continue
        f = {"name": go.get("m_Name", default=""), "parent": parent, "index": order.get(key, 0),
             "active": str(go.get("m_IsActive", default="1")) == "1"}
        if t is not None:
            p, q, s = coords.from_unity(vec(t.get("m_LocalPosition")), vec(t.get("m_LocalRotation"), "xyzw"),
                                        vec(t.get("m_LocalScale")), corrected=corrected[key],
                                        parent_corrected=corrected.get(parent, False))
            f["position"], f["rotation"], f["scale"] = p, q, s
        node = scene.add(Node(key, f))
        for comp in components(doc, go):
            _component(node, comp, corrected[key], doc, project, scene)
            if comp.class_id == MONOBEHAVIOUR:
                _script(node, comp, project, unpaired)

    for pi in [p for p in placed if prefab_root(project, project.path_of(source_of(p).get("guid", "")) or "").ui]:
        placed.remove(pi)  # a placed UI prefab: not synced (noted once above or here)
        if "UI (Canvas and RectTransform objects) isn't synced" not in scene.warnings:
            scene.warnings.append("UI (Canvas and RectTransform objects) isn't synced")
    for pi in placed:
        _prefab_instance(pi, node_of_transform, order, corrected, project, scene)

    if unpaired and getattr(project, "scripts", None) is not None:
        names = ", ".join(f"{p.rsplit('/', 1)[-1]} ({n})" for p, n in sorted(unpaired.items()))
        scene.warnings.append(f"scripts with no Godot partner stay on their objects in Unity only: {names}")
    for go, t, parent in parts:  # helpers fold back into their owner
        owner = scene.nodes.get(parent)
        if owner is None:
            continue
        part = Node("part")
        for comp in components(doc, go):
            _component(part, comp, False, doc, project, scene)
        for k, v in part.fields.items():
            if k.startswith(("mesh", "materials")):
                owner.fields[k] = v
        cyl = next((c for c in components(doc, go) if c.class_id == MESHCOLLIDER and
                    ref(c.get("m_Mesh")) == BUILTIN_CYLINDER), None)
        if cyl is not None and t is not None:  # a cylinder collider: Unity's cylinder mesh, scaled, in a helper
            p = free_collider_prefix(owner.fields)
            sx, sy, _ = vec(t.get("m_LocalScale"))
            owner.fields[f"{p}.shape"] = "cylinder"
            owner.fields[f"{p}.radius"] = round(sx / 2, 6)
            owner.fields[f"{p}.height"] = round(sy * 2, 6)
            center = coords.mirror_pos(vec(t.get("m_LocalPosition")))
            if corrected.get(parent):
                center = coords.qrot(coords.qconj(coords.HALF_TURN_Y), center)
            owner.fields[f"{p}.center"] = coords.clean(center)
            owner.fields[f"{p}.trigger"] = str(cyl.get("m_IsTrigger")) == "1"
        mesh = part.fields.get("mesh")
        if mesh and mesh[0] == "primitive" and t is not None:
            owner.fields["mesh.size"] = tuple(round(c, 6) for c in size_from_part_scale(mesh[1], vec(t.get("m_LocalScale"))))
        # a mesh helper may hold a placed model file (glTF, or a model on an object with physics); anything else
        # inside a helper is hidden from TwinScene
        for k, n in list(scene.nodes.items()):
            if n.parent == f"u:{go.file_id}":
                mesh = n.fields.get("mesh")
                if part_kind(doc, go) == "mesh" and mesh and mesh[0] == "asset" and not n.fields.get("mesh.name"):
                    owner.fields["mesh"] = mesh
                else:
                    scene.warnings.append(f"{n.name}: objects inside TwinScene helpers aren't synced")
                del scene.nodes[k]
    return scene


def _script(node: Node, c: yaml.UObject, project: UnityProject, unpaired: dict) -> None:
    """A MonoBehaviour: if its script has a Godot partner, the object carries it and the settings they share."""
    from .. import scripts as S
    table = getattr(project, "scripts", None)
    guid = (c.get("m_Script") or {}).get("guid", "")
    if table is None or not guid or guid == PART_SCRIPT_GUID:
        return
    pair = table.by_guid.get(guid)
    if pair is None:
        path = table.unity_paths.get(guid)
        if path:  # (scripts from packages aren't the team's to pair)
            unpaired[path] = unpaired.get(path, 0) + 1
        return
    f = node.fields
    names = list(f.get("scripts", ()))
    if pair.name in names:
        return  # the same script twice: only the first one syncs
    f["scripts"] = tuple(names + [pair.name])
    for k, (us, _) in pair.shared.items():
        raw = c.get(us.name)
        v = S.from_unity(raw, us.type) if raw is not None else us.default
        if v is not None:
            f[pair.field_name(k)] = v


def _center(c, corrected: bool) -> tuple:
    p = coords.mirror_pos(vec(c.get("m_Center")))
    if corrected:
        p = coords.qrot(coords.qconj(coords.HALF_TURN_Y), p)
    return coords.clean(p)


def _component(node: Node, c: yaml.UObject, corrected: bool, doc, project: UnityProject, scene: Scene) -> None:
    f = node.fields
    cid = c.class_id
    if cid == MESHFILTER:
        m = c.get("m_Mesh") or {}
        fid, guid = ref(m), m.get("guid", "")
        if guid == BUILTIN_EXTRA and fid in BUILTIN_MESHES:
            prim = BUILTIN_MESHES[fid]
            f["mesh"] = ("primitive", prim)
            f["mesh.size"] = PRIMITIVE_SIZE[prim]
        elif guid:
            path = project.path_of(guid)
            f["mesh"] = ("asset", project.ref(path) if path else f"unity:guid:{guid}")
            if path and path.lower().endswith(MODEL_EXTENSIONS):  # one mesh out of a model file
                names, scale = models.unity_model_info(project.path, path)
                if fid in names:
                    f["mesh.name"] = names[fid]
                    f["mesh.import_scale"] = scale
                    sp = models.fbx_scaling_pivots(project.path / path).get(names[fid])
                    if sp:  # Unity centres the mesh on its scaling pivot; Godot doesn't (models.py)
                        f["mesh.offset"] = tuple(round(-c * scale, 6) for c in sp)
                else:
                    scene.warnings.append(f"{node.name}: which mesh of {path.rsplit('/', 1)[-1]} it uses isn't "
                                          "recorded in its .meta (Unity 2019.3+ import); the whole model is used")
    elif cid == MESHRENDERER:
        mats = []
        for m in c.get("m_Materials", default=[]) or []:
            mats.append(_material(m, project, scene))
        f["materials"] = mats
    elif cid == CAMERA:
        f["camera.fov"] = num(c.get("field of view"), 60)
        f["camera.near"] = num(c.get("near clip plane"), 0.3)
        f["camera.far"] = num(c.get("far clip plane"), 1000)
        f["camera.ortho"] = str(c.get("orthographic")) == "1"
        f["camera.ortho_size"] = num(c.get("orthographic size"), 5)
    elif cid == LIGHT:
        lt = LIGHT_TYPES.get(int(num(c.get("m_Type"), 2)))
        if lt is None:
            scene.warnings.append(f"{node.name}: area lights aren't synced")
            return
        f["light.type"] = lt
        col = c.get("m_Color") or {}
        f["light.color"] = vec(col, "rgb")
        f["light.energy"] = num(c.get("m_Intensity"), 1)
        f["light.shadows"] = num(c.get("m_Shadows", "m_Type"), 0) != 0
        if lt in ("point", "spot"):
            f["light.range"] = num(c.get("m_Range"), 10)
        if lt == "spot":
            f["light.spot_angle"] = num(c.get("m_SpotAngle"), 30)
    elif cid in (BOXCOLLIDER, SPHERECOLLIDER, CAPSULECOLLIDER):
        p = free_collider_prefix(f)  # several colliders: collider.*, collider2.*, ...
        f[f"{p}.center"] = _center(c, corrected)
        f[f"{p}.trigger"] = str(c.get("m_IsTrigger")) == "1"
        if cid == BOXCOLLIDER:
            f[f"{p}.shape"] = "box"
            f[f"{p}.size"] = vec(c.get("m_Size"))
        elif cid == SPHERECOLLIDER:
            f[f"{p}.shape"] = "sphere"
            f[f"{p}.radius"] = num(c.get("m_Radius"), 0.5)
        else:
            f[f"{p}.shape"] = "capsule"
            f[f"{p}.radius"] = num(c.get("m_Radius"), 0.5)
            f[f"{p}.height"] = num(c.get("m_Height"), 2)
            if int(num(c.get("m_Direction"), 1)) != 1:
                scene.warnings.append(f"{node.name}: capsule along X/Z isn't synced yet (Y only)")
    elif cid == MESHCOLLIDER:
        m = c.get("m_Mesh") or {}
        if not (ref(m) == BUILTIN_CYLINDER and m.get("guid") == BUILTIN_EXTRA):  # (cylinders: see the helpers)
            scene.warnings.append(f"{node.name}: mesh colliders aren't synced yet")
    elif cid == RIGIDBODY:
        f["body.type"] = "kinematic" if str(c.get("m_IsKinematic")) == "1" else "rigid"
        f["body.mass"] = num(c.get("m_Mass"), 1)


def _material(m: dict, project: UnityProject, scene: Scene) -> str:
    fid, guid = ref(m), (m or {}).get("guid", "")
    if not fid:
        return ""
    if guid == BUILTIN_DEFAULT:
        return f"unity:builtin:{fid}"
    path = project.path_of(guid)
    if path is None:
        return f"unity:guid:{guid}"
    key = project.ref(path)
    if key not in scene.materials and path.endswith(".mat"):
        try:
            mdoc = yaml.load(project.path / path)
        except OSError:
            return key
        mat = next((o for o in mdoc.objects if o.class_id == MATERIAL), None)
        if mat is not None:
            scene.materials[key] = Material(key, read_material(mat, project))
    return key


def _props(mat: yaml.UObject, kind: str) -> dict:
    out = {}
    for item in mat.get("m_SavedProperties", kind, default=[]) or []:
        if isinstance(item, dict):
            out.update(item)
    return out


def read_material(mat: yaml.UObject, project: UnityProject) -> dict:
    """Built-in Standard and URP Lit -> neutral PBR fields."""
    colors, floats, tex = _props(mat, "m_Colors"), _props(mat, "m_Floats"), _props(mat, "m_TexEnvs")
    col = colors.get("_BaseColor") or colors.get("_Color") or {"r": 1, "g": 1, "b": 1, "a": 1}
    smooth = floats.get("_Smoothness", floats.get("_Glossiness", 0.5))
    f = {"name": mat.get("m_Name", default=""), "albedo": vec(col, "rgba"),
         "metallic": num(floats.get("_Metallic"), 0), "roughness": round(1 - num(smooth, 0.5), 6)}
    main = tex.get("_BaseMap") or tex.get("_MainTex")
    if isinstance(main, dict):
        t = main.get("m_Texture") or {}
        if t.get("guid"):
            p = project.path_of(t["guid"])
            if p:
                f["albedo_texture"] = project.ref(p)
    return f


@dataclass
class PrefabRoot:
    """The root object of a prefab or model file: what a placed copy looks like before its overrides."""
    go_id: int
    transform_id: int
    name: str
    position: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    scale: tuple = (1.0, 1.0, 1.0)
    active: bool = True
    corrected: bool = False  # the root is a camera or directional/spot light
    ui: bool = False  # the root is a UI object (RectTransform): not synced


_roots: dict = {}


def prefab_root(project: UnityProject, path: str) -> PrefabRoot:
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0] if path else "Prefab"
    if path.lower().endswith(GLTF_EXTENSIONS):  # glTFast's ids are hashes of names: learn them from overrides
        return PrefabRoot(0, 0, stem)
    if not path.lower().endswith(".prefab"):
        return PrefabRoot(ROOT_GO_ID, ROOT_TRANSFORM_ID, stem)
    f = project.path / path
    try:
        stamp = f.stat().st_mtime_ns
    except OSError:
        return PrefabRoot(ROOT_GO_ID, ROOT_TRANSFORM_ID, stem)
    hit = _roots.get(f)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    doc = yaml.load(f)
    root = PrefabRoot(0, 0, stem)
    for o in doc.objects:
        if o.class_id in (TRANSFORM, RECTTRANSFORM) and not o.stripped and ref(o.get("m_Father")) == 0:
            go = doc.by_id(ref(o.get("m_GameObject")))
            if go is None:
                continue
            root = PrefabRoot(go.file_id, o.file_id, str(go.get("m_Name", default=stem)),
                              vec(o.get("m_LocalPosition")), vec(o.get("m_LocalRotation"), "xyzw"),
                              vec(o.get("m_LocalScale")), str(go.get("m_IsActive", default="1")) == "1",
                              corrected_go(doc, go), o.class_id == RECTTRANSFORM)
            break
    _roots[f] = (stamp, root)
    return root


ROOT_GO_ID = 919132149155446097  # a model file's root GameObject and Transform, as Unity 2019.3+ names them
ROOT_TRANSFORM_ID = -8679921383154817045
_ROOT_FIELDS = ("m_LocalPosition.", "m_LocalRotation.", "m_LocalScale.", "m_LocalEulerAnglesHint.", "m_Name",
                "m_IsActive", "m_RootOrder", "m_ConstrainProportionsScale")


def _target_of(mods: list, prop: str) -> int:
    return next((ref(m.get("target")) for m in mods if isinstance(m, dict) and m.get("propertyPath") == prop), 0)


def _prefab_instance(pi: yaml.UObject, node_of_transform, order, corrected, project: UnityProject, scene: Scene):
    """A placed prefab or model file. Its objects aren't in the scene file, only the overrides."""
    key = f"u:{pi.file_id}"
    src = source_of(pi)
    path = project.path_of(src.get("guid", "")) or ""
    root = prefab_root(project, path)
    mods = pi.get("m_Modification", "m_Modifications", default=[]) or []
    if not root.transform_id:  # unknown ids (glTF): the root is what the position and name overrides aim at
        root = PrefabRoot(_target_of(mods, "m_Name"), _target_of(mods, "m_LocalPosition.x"), root.name)
    vals: dict[str, str] = {}
    inner = False
    for m in mods:
        if not isinstance(m, dict):
            continue
        prop, target = str(m.get("propertyPath", "")), ref(m.get("target"))
        if target == root.transform_id and prop.startswith("m_Local"):
            vals[prop] = m.get("value", "")
        elif target == root.go_id and prop in ("m_Name", "m_IsActive"):
            vals[prop] = m.get("value", "")
        elif target not in (root.go_id, root.transform_id) or not prop.startswith(_ROOT_FIELDS):
            inner = True  # aimed at something inside the prefab (or at more than the root's place and name)
    removed = (pi.get("m_Modification", "m_RemovedComponents", default=[]) or []) + \
              (pi.get("m_Modification", "m_RemovedGameObjects", default=[]) or [])
    parent = node_of_transform.get(ref(pi.get("m_Modification", "m_TransformParent")))
    pos = tuple(num(vals.get(f"m_LocalPosition.{a}"), d) for a, d in zip("xyz", root.position))
    rot = tuple(num(vals.get(f"m_LocalRotation.{a}"), d) for a, d in zip("xyzw", root.rotation))
    scl = tuple(num(vals.get(f"m_LocalScale.{a}"), d) for a, d in zip("xyz", root.scale))
    p, q, s = coords.from_unity(pos, rot, scl, corrected=root.corrected, parent_corrected=corrected.get(parent, False))
    name = vals.get("m_Name") or root.name
    active = vals.get("m_IsActive", "1" if root.active else "0") == "1"
    f = {"name": name, "parent": parent, "index": order.get(key, 0), "active": active,
         "position": p, "rotation": q, "scale": s}
    if path.lower().endswith(MODEL_EXTENSIONS):
        f["mesh"] = ("asset", project.ref(path))
    else:
        f["prefab"] = project.ref(path) if path else "unity:guid:" + src.get("guid", "")
    unsupported: set[str] = set()
    if path.lower().endswith(".prefab") and (inner or removed):
        found, unsupported = _overrides(mods, src.get("guid", ""), path, root, project, scene)
        f.update(found)
    elif inner:
        unsupported.add("model internals")
    if removed:
        unsupported.add("removed components or objects")
    if unsupported:
        scene.warnings.append(f"{name}: changes inside this placed prefab to {', '.join(sorted(unsupported))} "
                              "aren't synced yet")
    scene.add(Node(key, f))


def _overrides(mods: list, guid: str, path: str, root: PrefabRoot, project: UnityProject, scene: Scene):
    """Edits inside a placed prefab -> override fields (see overrides.py), and the edits that aren't synced."""
    from .. import overrides
    base = overrides.unity_base(project, path)
    if base is None:
        return {}, {"(prefab file missing)"}
    bdoc, bscene = base
    owners: dict[int, tuple] = {}  # object id in the prefab -> (its GameObject, class id, the object)
    for o in bdoc.objects:
        if o.stripped:
            continue
        if o.class_id == GAMEOBJECT:
            owners[o.file_id] = (o, GAMEOBJECT, o)
        elif ref(o.get("m_GameObject")):
            owners[o.file_id] = (bdoc.by_id(ref(o.get("m_GameObject"))), o.class_id, o)
    table = getattr(project, "scripts", None)
    edits: dict[int, dict] = {}
    unsupported: set[str] = set()
    for m in mods:
        if not isinstance(m, dict):
            continue
        prop, target = str(m.get("propertyPath", "")), m.get("target") or {}
        if target.get("guid") != guid:
            unsupported.add("objects of nested prefabs")
            continue
        own = owners.get(ref(target))
        if own is None or own[0] is None:
            if not prop.startswith(_ROOT_FIELDS):
                unsupported.add(prop)
            continue
        go, cls, comp = own
        pair = table.by_guid.get((comp.get("m_Script") or {}).get("guid", "")) if table and cls == MONOBEHAVIOUR else None
        setting = _script_setting(pair, prop) if pair is not None else None
        if setting is not None:  # a paired script's setting: "speed", or "axis.x" for one part of a vector
            s = edits.setdefault(go.file_id, {}).setdefault("scripts", {})
            s.setdefault((pair.name, setting), (pair, comp, {}))[2][prop] = m.get("value", "")
            continue
        if go.file_id == root.go_id and cls in (GAMEOBJECT, TRANSFORM, RECTTRANSFORM):
            continue  # the placed object's own name, place and visibility
        e = edits.setdefault(go.file_id, {})
        if cls in (TRANSFORM, RECTTRANSFORM) and prop.startswith(("m_LocalPosition.", "m_LocalRotation.", "m_LocalScale.")):
            e[prop] = m.get("value", "")
        elif cls == GAMEOBJECT and prop == "m_IsActive":
            e["active"] = str(m.get("value", "1"))
        elif cls == LIGHT and (prop in ("m_Intensity", "m_Range") or prop.startswith("m_Color.")):
            e[prop] = m.get("value", "")
        elif cls == MESHRENDERER and prop.startswith("m_Materials.Array.data["):
            e.setdefault("materials", {})[int(prop[len("m_Materials.Array.data["):-1])] = m.get("objectReference")
        elif not prop.startswith(("m_LocalEulerAnglesHint", "m_RootOrder", "m_ConstrainProportionsScale")):
            unsupported.add(prop)
    out: dict = {}
    for gofid, e in edits.items():
        key = f"u:{gofid}"
        basenode = bscene.nodes.get(key)
        go = bdoc.by_id(gofid)
        if basenode is None or go is None:
            continue
        eff: dict = {}
        if any(k.startswith("m_Local") for k in e):
            pos, rot, scl, _ = overrides.unity_inner_transform_raw(bdoc, go)
            pos = tuple(num(e.get(f"m_LocalPosition.{a}"), d) for a, d in zip("xyz", pos))
            rot = tuple(num(e.get(f"m_LocalRotation.{a}"), d) for a, d in zip("xyzw", rot))
            scl = tuple(num(e.get(f"m_LocalScale.{a}"), d) for a, d in zip("xyz", scl))
            parent_go = bdoc.by_id(int(basenode.parent[2:])) if basenode.parent else None
            eff["position"], eff["rotation"], eff["scale"] = coords.from_unity(
                pos, rot, scl, corrected=corrected_go(bdoc, go),
                parent_corrected=parent_go is not None and corrected_go(bdoc, parent_go))
        if "active" in e:
            eff["active"] = e["active"] == "1"
        light = next((c for c in components(bdoc, go) if c.class_id == LIGHT), None)
        if light is not None and any(k.startswith(("m_Color.", "m_Intensity", "m_Range")) for k in e):
            col = vec(light.get("m_Color") or {}, "rgb")
            eff["light.color"] = tuple(num(e.get(f"m_Color.{a}"), d) for a, d in zip("rgb", col))
            eff["light.energy"] = num(e.get("m_Intensity"), num(light.get("m_Intensity"), 1))
            eff["light.range"] = num(e.get("m_Range"), num(light.get("m_Range"), 10))
        if "materials" in e:
            rend = next((c for c in components(bdoc, go) if c.class_id == MESHRENDERER), None)
            mats = list((rend.get("m_Materials", default=[]) or []) if rend is not None else [])
            for i, m in e["materials"].items():
                while len(mats) <= i:
                    mats.append({})
                mats[i] = m
            eff["materials"] = [_material(m, project, scene) for m in mats]
        for (_, setting), (pair, comp, vals) in e.get("scripts", {}).items():
            eff[pair.field_name(setting)] = _script_override(pair, setting, comp, vals)
        out.update(overrides.differing(basenode.fields, eff, key))
    return out, unsupported


def _script_setting(pair, prop: str) -> str | None:
    """The shared setting a modification's propertyPath sets ("speed", "axis.x" -> "axis"), if any."""
    head = prop.split(".", 1)[0]
    for k, (us, _) in pair.shared.items():
        if us.name == head and (head == prop or us.type in ("vector2", "vector3", "color")):
            return k
    return None


def _script_override(pair, setting: str, comp: yaml.UObject, vals: dict):
    """A paired script setting's value in a placed prefab: the prefab's own value with the overridden parts."""
    from .. import scripts as S
    us = pair.shared[setting][0]
    if us.name in vals:
        return S.from_unity(vals[us.name], us.type)
    raw = comp.get(us.name)
    base = dict(raw) if isinstance(raw, dict) else {}
    for prop, v in vals.items():
        base[prop.split(".", 1)[1]] = v
    return S.from_unity(base, us.type)
