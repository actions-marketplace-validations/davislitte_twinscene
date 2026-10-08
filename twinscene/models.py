"""Model files (FBX, glTF, OBJ): which mesh inside them an object uses, and at what size.

Unity: an object shows one mesh from a model file, {fileID: <id>, guid: <model>}. The model's .meta names those ids
(fileIDToRecycleName before Unity 2019.3, internalIDToNameTable after). Unity bakes the import scale into the mesh:
the file's units (FBX UnitScaleFactor, centimetres by default) times the importer's globalScale.

Godot: the scene importer can save single meshes from a model as their own resources, set in the model's .import
file (_subresources > meshes > <name> > save_to_file). Godot keeps the file's own units in those meshes, so a
MeshInstance3D showing one needs the same scale Unity baked in.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from .godot import tscn, variant
from .godot.variant import Call

MESH_CLASS = 43  # Unity's Mesh class id (fileIDs 4300000, 4300002, ... in old tables)

_RECYCLE = re.compile(r"^\s{4}(-?\d+): (.+?)\s*$", re.M)
_INTERNAL = re.compile(r"- first:\s*\n\s*(\d+): (-?\d+)\s*\n\s*second: (.+?)\s*$", re.M)


# ---------------------------------------------------------------- Unity .meta


def unity_meshes(meta_text: str) -> dict[int, str]:
    """Mesh fileID -> mesh name, from a model's .meta (empty if the .meta doesn't list them)."""
    out: dict[int, str] = {}
    m = re.search(r"^\s*fileIDToRecycleName:\s*\n((?:\s{4}-?\d+: .*\n?)+)", meta_text, re.M)
    if m:
        for fid, name in _RECYCLE.findall(m.group(1)):
            if int(fid) // 100000 == MESH_CLASS:
                out[int(fid)] = _unquote(name)
    for cls, fid, name in _INTERNAL.findall(meta_text):
        if int(cls) == MESH_CLASS:
            out[int(fid)] = _unquote(name)
    return out


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1].replace("''", "'")
    return s


def unity_import_scale(meta_text: str, model: Path) -> float:
    """The scale Unity bakes into a model's meshes: globalScale times the file's units (if useFileScale)."""
    g = re.search(r"^\s*globalScale: ([\d.eE+-]+)", meta_text, re.M)
    use_file = re.search(r"^\s*useFileScale: (\d)", meta_text, re.M)
    scale = float(g.group(1)) if g else 1.0
    if use_file is None or use_file.group(1) == "1":
        scale *= file_unit_scale(model)
    return scale


def file_unit_scale(model: Path) -> float:
    """Metres per unit of a model file: FBX UnitScaleFactor / 100 (FBX counts in centimetres); 1 for the rest."""
    if model.suffix.lower() != ".fbx":
        return 1.0
    try:
        data = model.read_bytes()
    except OSError:
        return 1.0
    factor = None
    if data.startswith(b"Kaydara FBX Binary"):
        key = b"S" + struct.pack("<I", 15) + b"UnitScaleFactor"  # exactly this name, not OriginalUnitScaleFactor
        i = data.find(key)
        if i >= 0:  # Properties70 P record: "UnitScaleFactor" "double" "Number" "" D<value>
            j = i + len(key)
            for _ in range(3):
                if data[j:j + 1] != b"S":
                    break
                n = struct.unpack("<I", data[j + 1:j + 5])[0]
                j += 5 + n
            if data[j:j + 1] == b"D":
                factor = struct.unpack("<d", data[j + 1:j + 9])[0]
    else:
        m = re.search(rb'(?<!Original)"UnitScaleFactor",\s*"double",\s*"Number",\s*"",\s*([\d.eE+-]+)', data)
        if m:
            factor = float(m.group(1))
    return (factor if factor else 1.0) / 100.0


# ---------------------------------------------------------------- Godot .import

# Godot 4.7's defaults for the scene importer. A model's .import must be complete before Godot's first import, or
# Godot imports nothing (measured); TwinScene only adds _subresources to them.
SCENE_IMPORT_DEFAULTS = """nodes/root_type=""
nodes/root_name=""
nodes/root_script=null
mesh_library/use_node_names_as_mesh_names=false
array_mesh/deduplicate_surfaces=true
nodes/apply_root_scale=true
nodes/root_scale=1.0
nodes/import_as_skeleton_bones=false
nodes/use_name_suffixes=true
nodes/use_node_type_suffixes=true
meshes/ensure_tangents=true
meshes/generate_lods=true
meshes/create_shadow_meshes=true
meshes/light_baking=1
meshes/lightmap_texel_size=0.2
meshes/force_disable_compression=false
skins/use_named_skins=true
animation/import=true
animation/fps=30
animation/trimming=true
animation/remove_immutable_tracks=true
animation/import_rest_as_RESET=false
import_script/path=""
materials/extract=0
materials/extract_format=0
materials/extract_path=""
_subresources={}"""


# Per-format settings Godot writes itself. They matter: fbx/importer=0 is Godot's built-in FBX importer; when it's
# missing Godot picks the external FBX2glTF converter, usually not installed, and the import fails (measured).
FORMAT_DEFAULTS = {
    ".fbx": "fbx/importer=0\nfbx/allow_geometry_helper_nodes=false\nfbx/embedded_image_handling=1\nfbx/naming_version=2\n",
    ".gltf": "gltf/naming_version=2\ngltf/embedded_image_handling=1\n",
    ".glb": "gltf/naming_version=2\ngltf/embedded_image_handling=1\n",
}


def mesh_res_path(model_res: str, mesh_name: str) -> str:
    """Where TwinScene asks Godot to save one mesh of a model: next to it, as <model>_<mesh>.res."""
    stem = model_res.rsplit(".", 1)[0]
    safe = re.sub(r'[^\w\- ]', "_", mesh_name).strip() or "mesh"
    return f"{stem}_{safe}.res"


def ensure_extracted(project_path: Path, model_res: str, mesh_name: str) -> str:
    """Make the model's .import save this mesh as its own resource. Returns the mesh resource's res:// path."""
    target = mesh_res_path(model_res, mesh_name)
    f = project_path / (model_res[len("res://"):] + ".import")
    if f.is_file():
        doc = tscn.load(f)
    else:
        extra = FORMAT_DEFAULTS.get(Path(model_res).suffix.lower(), "")
        doc = tscn.loads('[remap]\n\nimporter="scene"\nimporter_version=1\ntype="PackedScene"\n\n[params]\n\n'
                         + SCENE_IMPORT_DEFAULTS + "\n" + extra)
    params = next((s for s in doc.sections if s.tag == "params"), None)
    if params is None:
        return target
    subs = params.value("_subresources") or {}
    subs = dict(subs) if isinstance(subs, dict) else {}
    meshes = dict(subs.get("meshes", {}))
    entry = dict(meshes.get(mesh_name, {}))
    if entry.get("save_to_file/enabled"):  # already saved somewhere (Godot rewrites our path as a uid + fallback)
        for key in ("save_to_file/fallback_path", "save_to_file/path"):
            if str(entry.get(key, "")).startswith("res://"):
                return entry[key]
    entry.update({"save_to_file/enabled": True, "save_to_file/path": target})
    meshes[mesh_name] = entry
    subs["meshes"] = meshes
    params.set("_subresources", subs)
    tscn.save(doc, f)
    return target


def extracted_meshes(project_path: Path) -> dict[str, tuple[str, str]]:
    """mesh resource res:// path -> (model res:// path, mesh name), from every model's .import file."""
    out: dict[str, tuple[str, str]] = {}
    for f in project_path.rglob("*.import"):
        if ".godot" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if '"meshes"' not in text:
            continue
        try:
            doc = tscn.loads(text)
        except variant.VariantError:
            continue
        params = next((s for s in doc.sections if s.tag == "params"), None)
        subs = params.value("_subresources") if params is not None else None
        if not isinstance(subs, dict):
            continue
        model = "res://" + f.relative_to(project_path).as_posix()[: -len(".import")]
        for name, entry in (subs.get("meshes") or {}).items():
            if isinstance(entry, dict) and entry.get("save_to_file/enabled"):
                path = entry.get("save_to_file/fallback_path") or entry.get("save_to_file/path")
                if isinstance(path, str) and path.startswith("res://"):
                    out[path] = (model, str(name))
    return out


def extracted_path(project_path: Path, model_res: str, mesh_name: str) -> str:
    """Where this mesh of the model is (or will be) saved, without changing anything."""
    f = project_path / (model_res[len("res://"):] + ".import")
    if f.is_file():
        try:
            params = next((s for s in tscn.load(f).sections if s.tag == "params"), None)
        except (OSError, variant.VariantError):
            params = None
        subs = params.value("_subresources") if params is not None else None
        entry = ((subs or {}).get("meshes") or {}).get(mesh_name) if isinstance(subs, dict) else None
        if isinstance(entry, dict) and entry.get("save_to_file/enabled"):
            for key in ("save_to_file/fallback_path", "save_to_file/path"):
                if str(entry.get(key, "")).startswith("res://"):
                    return entry[key]
    return mesh_res_path(model_res, mesh_name)


_meta_cache: dict = {}


def unity_model_info(unity_project: Path, model_path: str) -> tuple[dict[int, str], float]:
    """(mesh fileID -> name, import scale) for a model in a Unity project (cached by the .meta's timestamp)."""
    meta = unity_project / (model_path + ".meta")
    try:
        stamp = meta.stat().st_mtime_ns
    except OSError:
        return {}, 1.0
    hit = _meta_cache.get(meta)
    if hit is None or hit[0] != stamp:
        text = meta.read_text(encoding="utf-8", errors="replace")
        hit = (stamp, unity_meshes(text), unity_import_scale(text, unity_project / model_path))
        _meta_cache[meta] = hit
    return hit[1], hit[2]


# ---------------------------------------------------------------- FBX pivots
# Measured on ML-Agents' models: Unity always moves a mesh's vertices so its scaling pivot becomes the mesh's origin
# (and moves the node to match). Godot's importer (ufbx) does the same only when the rotation and scaling pivots are
# equal; otherwise it keeps the vertices as they are in the file. So for meshes with unequal pivots the same mesh sits
# -ScalingPivot apart in the two engines; TwinScene puts that offset on Godot's mesh helper.

def _fbx_nodes(data: bytes, i: int, end: int, ver: int) -> list:
    out = []
    head = 25 if ver >= 7500 else 13
    while i + head <= end:
        if ver >= 7500:
            eo, nprops, plen = struct.unpack("<QQQ", data[i:i + 24])
            i += 24
        else:
            eo, nprops, plen = struct.unpack("<III", data[i:i + 12])
            i += 12
        nlen = data[i]
        i += 1
        if eo == 0:
            break
        name = data[i:i + nlen].decode("latin-1")
        i += nlen
        props, j = [], i
        for _ in range(nprops):
            t = chr(data[j])
            j += 1
            if t in "SR":
                n = struct.unpack("<I", data[j:j + 4])[0]
                props.append(data[j + 4:j + 4 + n])
                j += 4 + n
            elif t == "D":
                props.append(struct.unpack("<d", data[j:j + 8])[0])
                j += 8
            elif t == "F":
                props.append(struct.unpack("<f", data[j:j + 4])[0])
                j += 4
            elif t == "I":
                props.append(struct.unpack("<i", data[j:j + 4])[0])
                j += 4
            elif t == "L":
                props.append(struct.unpack("<q", data[j:j + 8])[0])
                j += 8
            elif t == "Y":
                j += 2
                props.append(None)
            elif t == "C":
                j += 1
                props.append(None)
            elif t in "fdlib":
                _, _, clen = struct.unpack("<III", data[j:j + 12])
                j += 12 + clen
                props.append(None)
            else:
                break
        i += plen
        kids = _fbx_nodes(data, i, eo, ver) if i < eo else []
        out.append((name, props, kids))
        i = eo
    return out


_pivot_cache: dict = {}


def fbx_scaling_pivots(model: Path) -> dict[str, tuple]:
    """Mesh model name -> its ScalingPivot, for meshes that Unity and Godot centre differently: the pivot isn't at
    the origin and differs from the rotation pivot (binary FBX only)."""
    try:
        stamp = model.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _pivot_cache.get(model)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    out: dict[str, tuple] = {}
    try:
        data = model.read_bytes()
        if data.startswith(b"Kaydara FBX Binary"):
            ver = struct.unpack("<I", data[23:27])[0]
            objects = next((n for n in _fbx_nodes(data, 27, len(data), ver) if n[0] == "Objects"), None)
            for name, props, kids in (objects[2] if objects else []):
                if name != "Model" or len(props) < 3 or props[2] != b"Mesh":
                    continue
                p70 = next((k for k in kids if k[0] == "Properties70"), None)
                piv = {pp[0]: tuple(float(c) for c in pp[4:7]) for _, pp, _ in (p70[2] if p70 else [])
                       if pp and pp[0] in (b"ScalingPivot", b"RotationPivot") and len(pp) >= 7}
                sp, rp = piv.get(b"ScalingPivot", (0.0,) * 3), piv.get(b"RotationPivot", (0.0,) * 3)
                if any(abs(c) > 1e-9 for c in sp) and any(abs(a - b) > 1e-4 * max(1.0, abs(a)) for a, b in zip(sp, rp)):
                    out[props[1].split(b"\x00")[0].decode("utf-8", "replace")] = sp
    except (OSError, struct.error, IndexError, ValueError):
        out = {}
    _pivot_cache[model] = (stamp, out)
    return out
