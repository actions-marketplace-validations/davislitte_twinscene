"""Edits inside placed prefabs (overrides).

A placed copy of a prefab can change things inside it: one crate's lid light is red, another's is hidden. In TwinScene
those are fields on the placed object, named after the inner object they change:

    o:<inner key>:<field>        o:u:2323254309731663952:position     o:g:731183165:light.energy

<inner key> is that object's key in the prefab's own file, so the prefab pair's ID table (sync/<prefab>.json) pairs the
Unity and Godot names. A value is only an override when it differs from the prefab's own value, so redundant entries
(Unity keeps some, Godot's "Editable Children" writes every property) don't count.

Unity keeps overrides as m_Modifications aimed at the inner object's (or its component's) fileID in the prefab file;
Godot as a section for the inner node under the instance, plus [editable path="<instance>"].
"""

from __future__ import annotations

from pathlib import Path

from . import coords

FIELDS = ("position", "rotation", "scale", "active", "light.color", "light.energy", "light.range", "materials")
TRANSFORM = ("position", "rotation", "scale")


def name(inner_key: str, field: str) -> str:
    return f"o:{inner_key}:{field}"


def parse(field_name: str) -> tuple[str, str] | None:
    """'o:u:123:light.energy' -> ('u:123', 'light.energy')."""
    if not field_name.startswith("o:"):
        return None
    parts = field_name.split(":")
    if len(parts) != 4:
        return None
    return f"{parts[1]}:{parts[2]}", parts[3]


def of(fields: dict) -> dict[str, dict[str, object]]:
    """The override fields of a node, grouped: inner key -> {field: value}."""
    out: dict[str, dict] = {}
    for k, v in fields.items():
        p = parse(k)
        if p is not None:
            out.setdefault(p[0], {})[p[1]] = v
    return out


def differing(base: dict, effective: dict, inner_key: str) -> dict:
    """Override fields for one inner object: what differs from the prefab's own value."""
    from .model import same, same_rotation
    out = {}
    for f in FIELDS + tuple(sorted(k for k in effective if k.startswith("script."))):  # (paired scripts' settings)
        if f not in effective:
            continue
        a, b = base.get(f), effective[f]
        equal = same_rotation(a, b) if f == "rotation" and a is not None else same(a, b)
        if not equal:
            out[name(inner_key, f)] = b
    if any(name(inner_key, f) in out for f in TRANSFORM):  # transforms travel whole
        for f in TRANSFORM:
            out.setdefault(name(inner_key, f), effective.get(f, base.get(f)))
    return out


# ---------------------------------------------------------------- prefab bases (cached by file time)

_bases: dict = {}


def _cached(path: Path, build, extra=None):
    """build()'s result for this file, until the file changes (extra: anything else the result depends on)."""
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _bases.get((path, extra))
    if hit is None or hit[0] != stamp:
        hit = (stamp, build())
        _bases[(path, extra)] = hit
    return hit[1]


def _scripts_key(project):
    t = getattr(project, "scripts", None)
    return None if t is None else tuple(sorted(t.pairs))


def unity_base(project, prefab_path: str):
    """(prefab yaml document, its neutral Scene) for a Unity prefab, or None."""
    from .unity import reader, yaml
    f = project.path / prefab_path

    def build():
        doc = yaml.load(f)
        return doc, reader.read(doc, project)
    return _cached(f, build, _scripts_key(project))


def godot_base(project, res_path: str):
    """(scene document, its neutral Scene read as a prefab, path of each key) for a Godot scene, or None."""
    from .godot import reader, tscn
    f = project.path / res_path[len("res://"):]

    def build():
        doc = tscn.load(f)
        scene = reader.read(doc, project, res_path, prefab=True)
        paths = {}
        for k in scene.nodes:
            p = scene.path(k)
            paths[k] = p.split("/", 1)[1] if "/" in p else "."  # relative to the scene's root
        return doc, scene, paths
    return _cached(f, build, _scripts_key(project))


def unity_inner_transform_raw(base_doc, go):
    """An inner object's own Unity transform values (position, rotation, scale) in its prefab."""
    from .unity import reader as R
    from .unity.yaml import vec
    t = next((c for c in R.components(base_doc, go) if c.class_id in (R.TRANSFORM, R.RECTTRANSFORM)), None)
    if t is None:
        return (0.0, 0.0, 0.0), coords.IDENTITY, (1.0, 1.0, 1.0), None
    return vec(t.get("m_LocalPosition")), vec(t.get("m_LocalRotation"), "xyzw"), vec(t.get("m_LocalScale")), t
