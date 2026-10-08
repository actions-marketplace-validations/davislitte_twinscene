"""TwinScene's neutral scene model.

A scene is a set of nodes. Each node has a flat dict of fields, so diffing and merging is a dict comparison:

    name                    str
    parent                  node key or None
    index                   position among siblings (int)
    active                  bool (Unity: GameObject active; Godot: visible)
    position                (x, y, z)             } in TwinScene space: right-handed, Y up, metres,
    rotation                (x, y, z, w)          } the same as Godot and glTF. Unity is converted on
    scale                   (x, y, z)             } the way in and out (see coords.py).
    mesh                    ("primitive", "box"|"sphere"|"capsule"|"cylinder"|"plane"|"quad") or ("asset", path)
    mesh.size               (x, y, z) for primitives whose size Godot stores on the mesh
    materials               list of material keys
    camera.fov              vertical field of view, degrees
    camera.near / camera.far
    camera.ortho            bool; camera.ortho_size: half height
    light.type              "directional" | "point" | "spot"
    light.color             (r, g, b)
    light.energy / light.range
    light.spot_angle        full cone angle, degrees (Unity's convention; Godot stores half)
    light.shadows           bool
    collider.shape          "box" | "sphere" | "capsule"
    collider.center         (x, y, z) offset from the node
    collider.size / collider.radius / collider.height / collider.trigger
    body.type               "static" | "rigid" | "kinematic"
    body.mass

Node keys are each engine's own ID as a string ("u:<fileID>" or "g:<unique_id>"). The ID map in sync/ pairs them.
Fields a reader doesn't understand are simply absent; the engine files keep them (writers patch, they never
regenerate a known node from scratch).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


@dataclass
class Node:
    key: str
    fields: dict = field(default_factory=dict)

    def get(self, name: str, default=None):
        return self.fields.get(name, default)

    @property
    def name(self) -> str:
        return self.fields.get("name", "")

    @property
    def parent(self) -> str | None:
        return self.fields.get("parent")


@dataclass
class Material:
    key: str  # path of the material asset, or "<scene>#<id>" for one embedded in a scene
    fields: dict = field(default_factory=dict)  # name, albedo (r,g,b,a), metallic, roughness, albedo_texture


@dataclass
class Scene:
    nodes: dict[str, Node] = field(default_factory=dict)
    materials: dict[str, Material] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add(self, node: Node) -> Node:
        self.nodes[node.key] = node
        return node

    def children(self, key: str | None) -> list[Node]:
        kids = [n for n in self.nodes.values() if n.parent == key]
        return sorted(kids, key=lambda n: n.get("index", 0))

    def roots(self) -> list[Node]:
        return self.children(None)

    def path(self, key: str) -> str:
        parts, k = [], key
        while k is not None:
            n = self.nodes[k]
            parts.append(n.name)
            k = n.parent
        return "/".join(reversed(parts))

    def walk(self):
        """Depth-first, parents before children, in sibling order."""
        stack = list(reversed(self.roots()))
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(self.children(n.key)))

    # -- snapshots (sync/state/*.json)
    def to_json(self) -> dict:
        return {
            "nodes": {k: _jsonable(n.fields) for k, n in self.nodes.items()},
            "materials": {k: _jsonable(m.fields) for k, m in self.materials.items()},
        }

    @classmethod
    def from_json(cls, d: dict) -> "Scene":
        s = cls()
        for k, f in d.get("nodes", {}).items():
            s.nodes[k] = Node(k, _tuples(f))
        for k, f in d.get("materials", {}).items():
            s.materials[k] = Material(k, _tuples(f))
        return s

    def dumps(self) -> str:
        return json.dumps(self.to_json(), indent=1, sort_keys=True)


def _jsonable(fields: dict) -> dict:
    return {k: _round(list(v) if isinstance(v, tuple) else v) for k, v in fields.items()}


def _round(v):
    """Snapshot numbers to 6 decimals: comparisons allow far more than that, and the files stay tidy."""
    if isinstance(v, float):
        r = round(v, 6)
        return 0.0 if r == 0 else r
    if isinstance(v, list):
        return [_round(c) for c in v]
    return v


def _tuples(fields: dict) -> dict:
    return {k: (tuple(v) if isinstance(v, list) and k != "materials" else v) for k, v in fields.items()}


def same(a, b, tol: float = 1e-4) -> bool:
    """Field equality that ignores float noise from the round trip through text."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=tol, abs_tol=tol)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y, tol) for x, y in zip(a, b))
    return a == b


def same_rotation(a: Quat, b: Quat, tol: float = 1e-4) -> bool:
    """q and -q are the same rotation."""
    return same(a, b, tol) or same(a, tuple(-c for c in b), tol)


# ---------------------------------------------------------------- colliders
# An object can have several colliders: collider.* is the first, collider2.*, collider3.* ... the rest.

COLLIDER_KEYS = ("shape", "center", "size", "radius", "height", "trigger")


def collider_prefix(i: int) -> str:
    return "collider" if i == 0 else f"collider{i + 1}"


def is_collider_group(group: str) -> bool:
    return group == "collider" or (group.startswith("collider") and group[8:].isdigit())


def colliders(fields: dict) -> list[dict]:
    out, i = [], 0
    while f"{collider_prefix(i)}.shape" in fields:
        p = collider_prefix(i)
        out.append({k: fields[f"{p}.{k}"] for k in COLLIDER_KEYS if f"{p}.{k}" in fields})
        i += 1
    return out


def free_collider_prefix(fields: dict) -> str:
    i = 0
    while f"{collider_prefix(i)}.shape" in fields:
        i += 1
    return collider_prefix(i)
