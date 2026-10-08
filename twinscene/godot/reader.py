"""A Godot .tscn scene -> TwinScene's neutral model (Godot space is TwinScene space, so no axis conversion)."""

from __future__ import annotations

from .. import coords
from ..model import Material, Node, Scene, is_collider_group
from ..project import GodotProject
from . import tscn
from .variant import Call

# Godot leaves properties at their default value out of the file, so readers need the defaults.
DEFAULTS = {
    "Camera3D": {"fov": 75.0, "near": 0.05, "far": 4000.0, "projection": 0, "size": 1.0},
    "Light3D": {"light_color": Call("Color", (1, 1, 1, 1)), "light_energy": 1.0, "shadow_enabled": False},
    "OmniLight3D": {"omni_range": 5.0},
    "SpotLight3D": {"spot_range": 5.0, "spot_angle": 45.0},
    "BoxMesh": {"size": Call("Vector3", (1, 1, 1))},
    "SphereMesh": {"radius": 0.5, "height": 1.0},
    "CapsuleMesh": {"radius": 0.5, "height": 2.0},
    "CylinderMesh": {"top_radius": 0.5, "bottom_radius": 0.5, "height": 2.0},
    "PlaneMesh": {"size": Call("Vector2", (2, 2))},
    "QuadMesh": {"size": Call("Vector2", (1, 1))},
    "BoxShape3D": {"size": Call("Vector3", (1, 1, 1))},
    "SphereShape3D": {"radius": 0.5},
    "CapsuleShape3D": {"radius": 0.5, "height": 2.0},
    "CylinderShape3D": {"radius": 0.5, "height": 2.0},
    "WorldBoundaryShape3D": {"plane": Call("Plane", (0, 1, 0, 0))},
    "RigidBody3D": {"mass": 1.0},
    "StandardMaterial3D": {"albedo_color": Call("Color", (1, 1, 1, 1)), "metallic": 0.0, "roughness": 1.0},
}

PRIMITIVES = {"BoxMesh": "box", "SphereMesh": "sphere", "CapsuleMesh": "capsule", "CylinderMesh": "cylinder",
              "PlaneMesh": "plane", "QuadMesh": "quad"}
LIGHTS = {"DirectionalLight3D": "directional", "OmniLight3D": "point", "SpotLight3D": "spot"}
BODIES = {"StaticBody3D": "static", "RigidBody3D": "rigid", "CharacterBody3D": "kinematic",
          "AnimatableBody3D": "kinematic"}

UI_TYPES = {"CanvasLayer", "Control", "Label", "Button", "Panel", "TextureRect", "ColorRect", "NinePatchRect",
            "RichTextLabel", "ProgressBar", "TextureProgressBar", "LineEdit", "TextEdit", "CheckBox", "CheckButton",
            "OptionButton", "SpinBox", "HSlider", "VSlider", "ItemList", "Tree", "TabBar", "TabContainer",
            "ScrollContainer", "SubViewportContainer", "VideoStreamPlayer", "Viewport", "SubViewport"}


def is_ui(typ: str | None) -> bool:
    """2D and UI nodes aren't synced (Unity builds UI from Canvas objects)."""
    return bool(typ) and (typ in UI_TYPES or typ.endswith(("2D", "Container", "Button", "Separator")))


PART_META = "metadata/twinscene_part"  # marks a helper node TwinScene made (see writer); merged into its parent


def _val(sec: tscn.Section, key: str, *classes: str):
    v = sec.value(key)
    if v is not None:
        return v
    for c in classes:
        if key in DEFAULTS.get(c, {}):
            return DEFAULTS[c][key]
    return None


def _nums(v) -> tuple:
    return tuple(float(a) for a in v.args) if isinstance(v, Call) else ()


def transform_of(sec: tscn.Section):
    t = sec.value("transform")
    if not isinstance(t, Call) or len(t.args) != 12:
        return (0.0, 0.0, 0.0), coords.IDENTITY, (1.0, 1.0, 1.0)
    a = [float(x) for x in t.args]
    rows = [a[0:3], a[3:6], a[6:9]]
    q, s = coords.decompose(rows)
    return (a[9], a[10], a[11]), q, s


def read(doc: tscn.Document, project: GodotProject | None = None, scene_path: str = "", prefab: bool = False) -> Scene:
    """A level scene's root node stands for the scene itself: its children are the top-level nodes (parent None),
    matching a Unity scene's root objects. With prefab=True (a scene used like a Unity prefab) the root is a node
    too, matching the prefab's root object. TwinScene helper nodes fold back into the node they belong to.
    Placed scenes (instances) are single nodes with a "prefab" field; edits inside them aren't read yet."""
    project = project or GodotProject(".")
    scene = Scene()
    by_path: dict[str, str | None] = {}  # node path ("." for the root) -> key (None for the root)
    part_owner: dict[str, tuple[str, tuple, tuple]] = {}  # part path -> (owner key, position, rotation in owner)
    order: dict[str | None, int] = {}
    noted: set[str] = set()

    parent_types: dict[str, str] = {}  # node path -> its type, to know which shapes are inside an Area3D
    ui_paths: set[str] = set()
    instance_paths: dict[str, tuple] = {}  # path of a placed scene -> (its key, its section)
    edits: dict[str, list] = {}  # path of a placed scene -> [(inner path, override section)]
    for sec in doc.nodes():
        name = sec.get("name", "")
        parent_attr = sec.get("parent")
        parent_types["." if parent_attr is None else (name if parent_attr == "." else f"{parent_attr}/{name}")] =             sec.get("type") or ""
        if parent_attr is None:  # the root
            if sec.get("instance") is not None:
                scene.warnings.append(f"{name}: inherited scenes (Unity prefab variants) aren't synced yet")
            if prefab:
                uid = sec.get("unique_id")
                key = f"g:{uid}" if uid is not None else "g:path:."
                by_path["."] = key
                order[None] = 1
                _fill(scene.add(Node(key, {"name": name, "parent": None, "index": 0})), sec, doc, project, scene,
                      scene_path)
                continue
            by_path["."] = None
            pos, rot, scl = transform_of(sec)
            if not (pos == (0.0, 0.0, 0.0) and rot == coords.IDENTITY and scl == (1.0, 1.0, 1.0)):
                scene.warnings.append(f"{name}: the scene root's transform isn't synced")
            continue
        path = name if parent_attr == "." else f"{parent_attr}/{name}"
        uid = sec.get("unique_id")
        key = f"g:{uid}" if uid is not None else f"g:path:{path}"
        inst = _instance_holding(parent_attr, instance_paths)
        if inst is not None and sec.get("type") is None and sec.get("instance") is None:
            # an edit to a node that comes from a placed scene (an override), not a node of this scene
            inst_path, rel = inst
            edits.setdefault(inst_path, []).append(((rel + "/" if rel else "") + name, sec))
            continue
        if parent_attr not in by_path and parent_attr not in part_owner:
            # a node added under a node that only exists inside a placed scene
            top = inst[0] if inst is not None else _instance_above(parent_attr, by_path, doc)
            if top not in noted:
                noted.add(top)
                scene.warnings.append(f"{top}: nodes added inside this placed scene aren't synced yet")
            continue
        placed_ui = sec.get("instance") is not None and instance_is_ui(project, doc.resolve(sec.get("instance")))
        if is_ui(sec.get("type")) or parent_attr in ui_paths or placed_ui:
            ui_paths.add(path)
            if "ui" not in noted:
                noted.add("ui")
                scene.warnings.append("UI and 2D nodes aren't synced")
            continue
        if sec.get("type") is None and sec.get("instance") is None:
            scene.warnings.append(f"{path}: a node without a type, outside any placed scene; ignored")
            continue
        if uid is None:
            scene.warnings.append(f"{path}: no unique_id (saved by Godot before 4.6?); matched by path")
        if sec.value(PART_META) is not None:
            kind = str(sec.value(PART_META))
            pos, rot, _ = transform_of(sec)
            if parent_attr in part_owner:  # a part inside a part: put it in the owner's space
                owner, ppos, prot, chain = part_owner[parent_attr]
                pos = tuple(a + b for a, b in zip(ppos, coords.qrot(prot, pos)))
                rot = coords.qmul(prot, rot)
            else:
                owner, chain = by_path[parent_attr], ()
            if owner is None:
                scene.warnings.append(f"{path}: helper node directly under the scene root ignored")
                continue
            part_owner[path] = (owner, pos, rot, chain + (kind,))
            part = Node("part")
            _fill(part, sec, doc, project, scene, scene_path)
            of = scene.nodes[owner].fields
            if is_collider_group(kind) and "collider.shape" in part.fields:
                for k, v in part.fields.items():  # collider.* of the helper -> colliderN.* of the owner
                    if k.startswith("collider."):
                        of[kind + k[len("collider"):]] = v
                of[f"{kind}.center"] = coords.clean(pos)
                of[f"{kind}.trigger"] = "area" in chain or parent_types.get(parent_attr) == "Area3D"
            elif kind.startswith("script:"):  # a script that couldn't go on the owner itself
                if part.fields.get("scripts"):
                    of["scripts"] = tuple(of.get("scripts", ())) + tuple(
                        n for n in part.fields["scripts"] if n not in of.get("scripts", ()))
                of.update({k: v for k, v in part.fields.items() if k.startswith("script.")})
            else:
                for k, v in part.fields.items():
                    if k.startswith(("mesh", "materials", "camera.", "light.")):
                        of[k] = v
                if kind == "mesh" and "mesh.name" in part.fields:  # its scale and offset come from the model file
                    t = transform_of(sec)
                    of["mesh.import_scale"] = round(t[2][0], 6)
                    if any(abs(c) > 1e-6 for c in t[0]):
                        of["mesh.offset"] = tuple(round(c, 6) for c in coords.qrot(coords.qconj(t[1]), t[0]))
            continue
        if parent_attr in part_owner:
            scene.warnings.append(f"{path}: nodes inside TwinScene helper nodes aren't synced")
            continue
        parent = by_path[parent_attr]
        by_path[path] = key
        idx = order.get(parent, 0)
        order[parent] = idx + 1
        node = scene.add(Node(key, {"name": name, "parent": parent, "index": idx}))
        _fill(node, sec, doc, project, scene, scene_path)
        if sec.get("instance") is not None and node.fields.get("prefab"):
            instance_paths[path] = (key, sec)
        if "collider.shape" in node.fields:
            node.fields["collider.trigger"] = parent_types.get(parent_attr) == "Area3D"
    table = getattr(project, "scripts", None)
    if table is not None:
        unpaired: dict[str, int] = {}
        for sec in doc.nodes():
            res = doc.resolve(sec.value("script")) if sec.value("script") is not None else None
            path = res.get("path", "") if res is not None else ""
            if path in table.godot_paths and path not in table.by_res:
                unpaired[path] = unpaired.get(path, 0) + 1
        if unpaired:
            names = ", ".join(f"{p.rsplit('/', 1)[-1]} ({n})" for p, n in sorted(unpaired.items()))
            scene.warnings.append(f"scripts with no Unity partner stay on their nodes in Godot only: {names}")
    for inst_path, (key, sec) in instance_paths.items():
        found, unsupported = _overrides(scene.nodes[key], sec, edits.get(inst_path, []), doc, project, scene,
                                        scene_path)
        scene.nodes[key].fields.update(found)
        if unsupported:
            scene.warnings.append(f"{inst_path}: changes inside this placed scene to {', '.join(sorted(unsupported))} "
                                  "aren't synced yet")
    return scene


def _instance_holding(parent_path: str | None, instance_paths: dict) -> tuple[str, str] | None:
    """(placed scene's path, path inside it) if parent_path is a placed scene or inside one."""
    if parent_path is None:
        return None
    parts = parent_path.split("/")
    for i in range(len(parts), 0, -1):
        head = "/".join(parts[:i])
        if head in instance_paths:
            return head, "/".join(parts[i:])
    return None


_EDIT_PROPS = ("transform", "visible", "light_color", "light_energy", "omni_range", "spot_range", "unique_id")


def _overrides(node: Node, inst_sec, edits: list, doc, project: GodotProject, scene: Scene, scene_path: str):
    """A placed scene's edits (its inner nodes' sections, and the instance's own extra properties) -> override fields."""
    from .. import overrides, scripts as S
    res = doc.resolve(inst_sec.get("instance"))
    base = overrides.godot_base(project, res.get("path", "")) if res is not None else None
    unsupported: set[str] = set()
    if base is None:
        return {}, ({"(scene file missing)"} if edits else set())
    _, bscene, paths = base
    by_rel = {p: k for k, p in paths.items()}
    root_key = next((k for k, p in paths.items() if p == "."), None)
    out: dict = {}
    targets = [(rel, sec, False) for rel, sec in edits] + [(".", inst_sec, True)]
    for rel, sec, is_root in targets:
        key = root_key if is_root else (f"g:{sec.get('unique_id')}" if f"g:{sec.get('unique_id')}" in bscene.nodes
                                        else by_rel.get(rel))
        basenode = bscene.nodes.get(key) if key else None
        if basenode is None:
            if not is_root:
                unsupported.add(rel)
            continue
        eff: dict = {}
        if not is_root:
            if sec.value("transform") is not None:
                eff["position"], eff["rotation"], eff["scale"] = transform_of(sec)
            if sec.value("visible") is not None:
                eff["active"] = bool(sec.value("visible"))
        if sec.value("light_color") is not None:
            eff["light.color"] = _nums(sec.value("light_color"))[:3]
        if sec.value("light_energy") is not None:
            eff["light.energy"] = float(sec.value("light_energy"))
        for prop in ("omni_range", "spot_range"):
            if sec.value(prop) is not None:
                eff["light.range"] = float(sec.value(prop))
        surf = sorted((int(p.key.rsplit("/", 1)[1]), p.value) for p in sec.props
                      if p.key.startswith("surface_material_override/"))
        if surf:
            mats = list(basenode.fields.get("materials", []))
            for i, ref in surf:
                while len(mats) <= i:
                    mats.append("")
                mats[i] = _material(ref, doc, project, scene, scene_path)
            eff["materials"] = mats
        handled = set()
        for pair in script_pairs_on(project, base[0], paths.get(key, "."), basenode):
            for k, (_, gs) in pair.shared.items():
                v = S.from_godot(sec.value(gs.name), gs.type)
                if v is not None:
                    eff[pair.field_name(k)] = v
                handled.add(gs.name)
        if not is_root:
            for prop in sec.props:
                if prop.key and not prop.key.startswith(("surface_material_override/", "metadata/")) and \
                        prop.key not in _EDIT_PROPS and prop.key not in handled:
                    unsupported.add(prop.key)
        out.update(overrides.differing(basenode.fields, eff, key))
    return out, unsupported


def script_pairs_on(project: GodotProject, doc: tscn.Document, rel: str, node) -> list:
    """The paired scripts on the node at rel (relative to the scene's root) itself, not on its helpers."""
    table = getattr(project, "scripts", None)
    if table is None or not node.fields.get("scripts"):
        return []
    for sec in doc.nodes():
        pa = sec.get("parent")
        path = "." if pa is None else (sec.get("name") if pa == "." else f"{pa}/{sec.get('name')}")
        if path == rel:
            res = doc.resolve(sec.value("script")) if sec.value("script") is not None else None
            pair = table.by_res.get(res.get("path", "")) if res is not None else None
            return [pair] if pair is not None else []
    return []


def _instance_above(path: str, by_path: dict, doc: tscn.Document) -> str:
    parts = path.split("/")
    for i in range(len(parts), 0, -1):
        if "/".join(parts[:i]) in by_path:
            return "/".join(parts[:i])
    return path


SCENE_FILES = (".tscn", ".scn")


def instance_is_ui(project: GodotProject, ext) -> bool:
    """Is this placed scene a UI scene (its root a Control or 2D node)?"""
    path = ext.get("path", "") if ext is not None else ""
    if not path.endswith(".tscn"):
        return False
    try:
        nodes = tscn.load(project.path / path[len("res://"):]).nodes()
    except (OSError, ValueError):
        return False
    return bool(nodes) and is_ui(nodes[0].get("type"))
_root_cache: dict = {}


def instance_default(project: GodotProject, res_path: str):
    """A placed scene's transform when the placing scene doesn't set one: its root node's own transform."""
    if not res_path.endswith(".tscn"):  # (binary .scn files can't be read)
        return (0.0, 0.0, 0.0), coords.IDENTITY, (1.0, 1.0, 1.0)
    f = project.path / res_path[len("res://"):]
    try:
        stamp = f.stat().st_mtime_ns
    except OSError:
        return (0.0, 0.0, 0.0), coords.IDENTITY, (1.0, 1.0, 1.0)
    hit = _root_cache.get(f)
    if hit is None or hit[0] != stamp:
        doc = tscn.load(f)
        root = doc.nodes()[0] if doc.nodes() else None
        hit = (stamp, transform_of(root) if root is not None else ((0.0, 0.0, 0.0), coords.IDENTITY, (1.0, 1.0, 1.0)))
        _root_cache[f] = hit
    return hit[1]


def _fill(node: Node, sec: tscn.Section, doc: tscn.Document, project: GodotProject, scene: Scene, scene_path: str):
    f = node.fields
    typ = sec.get("type")
    inst = doc.resolve(sec.get("instance")) if sec.get("instance") is not None else None
    if sec.get("instance") is not None:
        # A placed scene: what it is and where it goes. Its other properties come from the scene it places.
        res_path = inst.get("path", "") if inst is not None else ""
        if res_path.endswith(".scn"):
            scene.warnings.append(f"{sec.get('name')}: {res_path} is a binary scene, which TwinScene can't read; "
                                  "save it as .tscn to sync it")
        elif res_path.endswith(SCENE_FILES):
            f["prefab"] = project.ref(res_path)
        elif res_path:
            f["mesh"] = ("asset", project.ref(res_path))
        vis = sec.value("visible")
        f["active"] = True if vis is None else bool(vis)
        f["position"], f["rotation"], f["scale"] = (transform_of(sec) if sec.value("transform") is not None
                                                    else instance_default(project, res_path))
        return
    vis = sec.value("visible")
    f["active"] = True if vis is None else bool(vis)
    _script(f, sec, doc, project)
    pos, rot, scl = transform_of(sec)
    if sec.value("position") is not None:  # rare: some tools write position/rotation instead of transform
        pos = _nums(sec.value("position"))
    f["position"], f["rotation"], f["scale"] = pos, rot, scl

    if typ == "MeshInstance3D":
        mesh_ref = sec.value("mesh")
        mesh = doc.resolve(mesh_ref)
        mats = []
        if mesh is not None and mesh.tag == "sub_resource" and mesh.get("type") in PRIMITIVES:
            mtype = mesh.get("type")
            prim = PRIMITIVES[mtype]
            f["mesh"] = ("primitive", prim)
            if prim == "box":
                f["mesh.size"] = _nums(_val(mesh, "size", mtype))
            elif prim in ("plane", "quad"):
                f["mesh.size"] = _nums(_val(mesh, "size", mtype))
            elif prim == "sphere":
                f["mesh.size"] = (float(_val(mesh, "radius", mtype)), float(_val(mesh, "height", mtype)))
            else:
                f["mesh.size"] = (float(_val(mesh, "radius" if prim == "capsule" else "top_radius", mtype)),
                                  float(_val(mesh, "height", mtype)))
            if mesh.value("material") is not None:
                mats.append(_material(mesh.value("material"), doc, project, scene, scene_path))
        elif mesh is not None and mesh.tag == "ext_resource":
            path = mesh.get("path", "")
            one = project.extracted().get(path)  # one mesh saved out of a model file
            if one is not None:
                f["mesh"] = ("asset", project.ref(one[0]))
                f["mesh.name"] = one[1]
                f["mesh.import_scale"] = 1.0
            else:
                f["mesh"] = ("asset", project.ref(path))
        elif mesh_ref is not None:
            f["mesh"] = ("asset", f"godot:{scene_path}#{mesh_ref.args[0] if isinstance(mesh_ref, Call) else '?'}")
        override = sec.value("material_override")
        if override is not None:
            mats = [_material(override, doc, project, scene, scene_path)]
        surf = sorted(p.key for p in sec.props if p.key.startswith("surface_material_override/"))
        if surf:
            mats = [_material(sec.value(k), doc, project, scene, scene_path) for k in surf]
        if mats:
            f["materials"] = mats

    elif typ == "Camera3D":
        f["camera.fov"] = float(_val(sec, "fov", "Camera3D"))
        f["camera.near"] = float(_val(sec, "near", "Camera3D"))
        f["camera.far"] = float(_val(sec, "far", "Camera3D"))
        f["camera.ortho"] = _val(sec, "projection", "Camera3D") == 1
        f["camera.ortho_size"] = float(_val(sec, "size", "Camera3D")) / 2

    elif typ in LIGHTS:
        f["light.type"] = LIGHTS[typ]
        col = _nums(_val(sec, "light_color", "Light3D"))
        f["light.color"] = col[:3]
        f["light.energy"] = float(_val(sec, "light_energy", "Light3D"))
        f["light.shadows"] = bool(_val(sec, "shadow_enabled", "Light3D"))
        if typ == "OmniLight3D":
            f["light.range"] = float(_val(sec, "omni_range", typ))
        elif typ == "SpotLight3D":
            f["light.range"] = float(_val(sec, "spot_range", typ))
            f["light.spot_angle"] = float(_val(sec, "spot_angle", typ)) * 2  # Godot stores the half angle

    elif typ == "CollisionShape3D":
        shape = doc.resolve(sec.value("shape"))
        st = shape.get("type") if shape is not None else None
        if st == "BoxShape3D":
            f["collider.shape"] = "box"
            f["collider.size"] = _nums(_val(shape, "size", st))
        elif st == "SphereShape3D":
            f["collider.shape"] = "sphere"
            f["collider.radius"] = float(_val(shape, "radius", st))
        elif st == "CapsuleShape3D":
            f["collider.shape"] = "capsule"
            f["collider.radius"] = float(_val(shape, "radius", st))
            f["collider.height"] = float(_val(shape, "height", st))
        elif st == "CylinderShape3D":
            f["collider.shape"] = "cylinder"
            f["collider.radius"] = float(_val(shape, "radius", st))
            f["collider.height"] = float(_val(shape, "height", st))
        elif st == "WorldBoundaryShape3D":
            # An endless plane. Unity has none: it gets a very large, thin box just under the plane.
            nx, ny, nz, d = _nums(_val(shape, "plane", st))
            if abs(nx) < 1e-6 and abs(nz) < 1e-6 and ny > 0:
                f["collider.shape"] = "box"
                f["collider.size"] = (10000.0, 1.0, 10000.0)
                f["collider.boundary"] = True
                f["collider.center_y"] = d / ny - 0.5
                scene.warnings.append("endless floor planes (WorldBoundaryShape3D) become a 10 km box in Unity")
            else:
                scene.warnings.append(f"{node.name}: tilted WorldBoundaryShape3D isn't synced yet")
        elif st is not None:
            scene.warnings.append(f"{node.name}: collision shape {st} isn't synced yet")
        if "collider.shape" in f:
            f["collider.center"] = (0.0, f.pop("collider.center_y", 0.0), 0.0)

    elif typ in BODIES:
        f["body.type"] = BODIES[typ]
        if typ == "RigidBody3D":
            f["body.mass"] = float(_val(sec, "mass", "RigidBody3D"))


def _script(f: dict, sec: tscn.Section, doc: tscn.Document, project: GodotProject) -> None:
    """A node's script: if it has a Unity partner, the node carries it and the settings they share."""
    from .. import scripts as S
    table = getattr(project, "scripts", None)
    res = doc.resolve(sec.value("script")) if table is not None and sec.value("script") is not None else None
    pair = table.by_res.get(res.get("path", "")) if res is not None else None
    if pair is None:
        return
    f["scripts"] = (pair.name,)
    for k, (_, gs) in pair.shared.items():
        raw = sec.value(gs.name)
        v = S.from_godot(raw, gs.type) if raw is not None else gs.default
        if v is not None:
            f[pair.field_name(k)] = v


def _material(ref, doc: tscn.Document, project: GodotProject, scene: Scene, scene_path: str) -> str:
    res = doc.resolve(ref)
    if res is None:
        return ""
    if res.tag == "ext_resource":
        key = project.ref(res.get("path", ""))
        if key not in scene.materials and res.get("path", "").endswith(".tres"):
            fields = read_material_file(project, res.get("path"))
            if fields is not None:
                scene.materials[key] = Material(key, fields)
        return key
    key = f"godot:{scene_path}#{res.get('id')}"
    if key not in scene.materials and res.get("type") == "StandardMaterial3D":
        scene.materials[key] = Material(key, material_fields(res, doc, project))
    return key


def read_material_file(project: GodotProject, res_path: str) -> dict | None:
    """A StandardMaterial3D saved as its own .tres -> neutral fields (None if it's another kind of material)."""
    try:
        doc = tscn.load(project.path / res_path[len("res://"):])
    except OSError:
        return None
    head = doc.header
    res = doc.by_tag("resource")
    if head is None or head.get("type") != "StandardMaterial3D" or not res:
        return None
    return material_fields(res[0], doc, project)


def material_fields(res: tscn.Section, doc: tscn.Document, project: GodotProject) -> dict:
    """A StandardMaterial3D section (a .tres [resource] or a scene's sub_resource) -> neutral fields."""
    t = "StandardMaterial3D"
    col = _nums(_val(res, "albedo_color", t))
    mf = {"albedo": col, "metallic": float(_val(res, "metallic", t)), "roughness": float(_val(res, "roughness", t))}
    tex = doc.resolve(res.value("albedo_texture"))
    if tex is not None and tex.get("path"):
        mf["albedo_texture"] = project.ref(tex.get("path"))
    name = res.value("resource_name")
    if name:
        mf["name"] = name
    return mf
