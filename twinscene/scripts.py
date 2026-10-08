"""Script pairing: which Unity C# script and which Godot script (GDScript or C#) play the same part, and which of
their inspector settings are the same setting.

TwinScene never reads or translates game code. It only needs to know, for each paired script:

- where it is in each engine (to attach it to objects the other side added), and
- its settings (Unity: serialized fields; Godot: @export / [Export] members), to sync their values.

Scripts pair automatically when their file names match once written the same way (PlayerController.cs ~
player_controller.gd ~ PlayerController.cs in Godot), and explicitly in twinscene.json:

    "scripts": {"Assets/Scripts/Enemy.cs": "res://ai/enemy_brain.gd"}

Settings pair the same way: moveSpeed, m_MoveSpeed, MoveSpeed and move_speed are all "move_speed". Only plain values
sync: numbers, true/false, text, Vector2, Vector3 and Color. In the neutral model an object gets

    scripts                       ["Enemy", ...]   the paired scripts on it, in order
    script.Enemy.move_speed       5.0              one per shared setting

A Vector3 setting is converted like a position (Unity mirrors X; see coords.py). *Assumed*: script vectors are
positions, offsets or directions in the object's world, which holds for most gameplay settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .project import Config

TYPES = ("float", "int", "bool", "string", "vector2", "vector3", "color")
_NUMERIC = ("float", "int")

_CS_TYPES = {"float": "float", "double": "float", "int": "int", "long": "int", "short": "int", "byte": "int",
             "uint": "int", "bool": "bool", "string": "string", "String": "string", "Vector3": "vector3",
             "Vector2": "vector2", "Color": "color"}
_GD_TYPES = {"float": "float", "int": "int", "bool": "bool", "String": "string", "StringName": "string",
             "Vector3": "vector3", "Vector2": "vector2", "Color": "color"}

# Godot's built-in node classes TwinScene makes or meets, each with its parent class (to check where a script can go:
# Godot only attaches a script to a node of the class it extends, or a subclass).
GODOT_PARENT = {
    "Node3D": "Node", "VisualInstance3D": "Node3D", "GeometryInstance3D": "VisualInstance3D",
    "MeshInstance3D": "GeometryInstance3D", "CollisionObject3D": "Node3D", "PhysicsBody3D": "CollisionObject3D",
    "StaticBody3D": "PhysicsBody3D", "AnimatableBody3D": "StaticBody3D", "RigidBody3D": "PhysicsBody3D",
    "CharacterBody3D": "PhysicsBody3D", "Area3D": "CollisionObject3D", "CollisionShape3D": "Node3D",
    "Camera3D": "Node3D", "Light3D": "VisualInstance3D", "DirectionalLight3D": "Light3D", "OmniLight3D": "Light3D",
    "SpotLight3D": "Light3D", "Marker3D": "Node3D", "Path3D": "Node3D", "PathFollow3D": "Node3D",
    "AudioStreamPlayer3D": "Node3D", "RayCast3D": "Node3D", "Timer": "Node", "AudioStreamPlayer": "Node",
    "AnimationMixer": "Node", "AnimationPlayer": "AnimationMixer", "Node": None,
    "RefCounted": None, "Resource": "RefCounted",  # (not nodes: a script extending these can't go on one)
}


def godot_is_a(typ: str | None, base: str | None) -> bool | None:
    """Is node class typ the class base or a subclass of it? None if TwinScene doesn't know one of them."""
    if not typ or not base:
        return None
    if typ not in GODOT_PARENT or base not in GODOT_PARENT:
        return None
    t: str | None = typ
    while t is not None:
        if t == base:
            return True
        t = GODOT_PARENT.get(t)
    return False


def snake(name: str) -> str:
    """moveSpeed, m_MoveSpeed, MoveSpeed, _move_speed, MOVE_SPEED -> move_speed."""
    n = re.sub(r"^(m_|_+)", "", name)
    n = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", n)
    n = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", n)
    return re.sub(r"_+", "_", n).strip("_").lower()


@dataclass
class Setting:
    name: str  # as the engine names it (Unity field / Godot property)
    type: str  # one of TYPES, or "" for a kind TwinScene doesn't sync
    default: object = None  # in TwinScene terms; None = unknown
    type_text: str = ""  # as written in the script, for messages


@dataclass
class ScriptInfo:
    engine: str  # "unity" | "godot"
    path: str  # Unity: Assets/...cs; Godot: res://...
    cls: str = ""
    base: str = ""  # the class it extends (Godot: resolved to a built-in class when it can be)
    settings: dict[str, Setting] = field(default_factory=dict)  # neutral name -> setting
    class_name: str = ""  # GDScript class_name


@dataclass
class ScriptPair:
    name: str
    unity: ScriptInfo
    godot: ScriptInfo
    guid: str  # the Unity script's guid ("" if Unity hasn't made its .meta yet)
    shared: dict[str, tuple[Setting, Setting]] = field(default_factory=dict)  # neutral name -> (unity, godot)
    only_unity: list[str] = field(default_factory=list)
    only_godot: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)  # same name, different kinds of value
    unsupported: list[str] = field(default_factory=list)  # kinds of value TwinScene doesn't sync

    def field_name(self, setting: str) -> str:
        return f"script.{self.name}.{setting}"


# ---------------------------------------------------------------- C# (Unity and Godot)

_CS_ACCESS = {"public", "private", "protected", "internal"}


def _cs_strip(text: str) -> str:
    """Comments out, strings kept (defaults can be strings)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif c == '"' or text.startswith('@"', i) or text.startswith('$"', i):
            j = i + (1 if c == '"' else 2)
            verbatim = text.startswith('@"', i)
            while j < n:
                if text[j] == "\\" and not verbatim:
                    j += 2
                    continue
                if text[j] == '"':
                    if verbatim and text.startswith('""', j):
                        j += 2
                        continue
                    break
                j += 1
            out.append(text[i:j + 1])
            i = j + 1
        elif c == "'":
            j = i + 1
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _cs_class_bodies(code: str):
    """(class name, base, body start, body end) for each class (not struct/enum) at any depth."""
    for m in re.finditer(r"\bclass\s+([A-Za-z_]\w*)(?:\s*<[^>{]*>)?\s*(?::\s*([^{]+))?\{", code):
        start = m.end()
        depth, i = 1, start
        while i < len(code) and depth:
            if code[i] in "\"'":
                i = _skip_literal(code, i)
                continue
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
            i += 1
        bases = (m.group(2) or "").split(",")
        yield m.group(1), bases[0].strip().split(".")[-1] if bases[0].strip() else "", start, i - 1


def _skip_literal(code: str, i: int) -> int:
    """The index just after the string or char literal starting at i (escapes and @"verbatim" strings handled)."""
    q = code[i]
    verbatim = q == '"' and i > 0 and code[i - 1] == "@"
    j = i + 1
    while j < len(code):
        if code[j] == "\\" and not verbatim:
            j += 2
            continue
        if code[j] == q:
            if verbatim and code.startswith('""', j):
                j += 2
                continue
            return j + 1
        j += 1
    return len(code)


def _cs_members(body: str) -> list[tuple[str, bool]]:
    """Statements at the class body's own level: (text with blocks removed, was a property or other block)."""
    out, buf, depth, block, i = [], [], 0, False, 0
    while i < len(body):
        c = body[i]
        if c in "\"'":
            j = _skip_literal(body, i)
            if depth == 0:
                buf.append(body[i:j])
            i = j
            continue
        if c == "{":
            if depth == 0:
                block = True
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                text = "".join(buf)
                rest = body[i + 1:].lstrip()
                if "(" in text.split("=")[0] or re.search(r"\b(class|struct|enum|interface)\b", text) \
                        or not rest.startswith("="):
                    out.append((text, True))  # a method, a nested type, or a property without a starting value
                    buf, block = [], False
        elif depth == 0:
            if c == ";":
                out.append(("".join(buf), block))
                buf, block = [], False
            else:
                buf.append(c)
        i += 1
    return out


def _leading_attributes(stmt: str) -> tuple[str, str]:
    """'[SerializeField, Range(0, 1)] [Tooltip("x")] float speed' -> (the attributes' text, "float speed").
    Only leading ones, so array types like float[] stay as they are."""
    s, attrs = stmt.strip(), []
    while s.startswith("["):
        depth, j = 0, 0
        while j < len(s):
            if s[j] in "\"'":
                j = _skip_literal(s, j)
                continue
            if s[j] == "[":
                depth += 1
            elif s[j] == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        attrs.append(s[1:j])
        s = s[j + 1:].strip()
    return " ".join(attrs), s


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(m.group(1), m.group(1)), s)


def _cs_value(text: str, typ: str, engine: str):
    """A C# starting value -> TwinScene value (None if it isn't a plain one)."""
    t = text.strip()
    if typ in _NUMERIC:
        m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)[fFdDmMlLuU]*", t)
        if not m:
            return None
        v = float(m.group(1))
        return int(v) if typ == "int" else v
    if typ == "bool":
        return {"true": True, "false": False}.get(t)
    if typ == "string":
        if t == "null":
            return ""
        m = re.fullmatch(r'@?"(.*)"', t, re.S)
        if not m:
            return None
        return m.group(1).replace('""', '"') if t.startswith("@") else _unescape(m.group(1))
    if typ in ("vector3", "vector2"):
        n = 3 if typ == "vector3" else 2
        m = re.fullmatch(r"new\s*(?:\w+(?:\.\w+)*)?\s*\(([^()]*)\)", t)
        if m:
            parts = [p for p in m.group(1).split(",") if p.strip()]
            vals = [_cs_value(p, "float", engine) for p in parts]
            if not parts:
                return (0.0,) * n
            if len(vals) == n and None not in vals:
                v = tuple(vals)
                return _from_unity_vec(v) if engine == "unity" and n == 3 else v
            return None
        const = t.rsplit(".", 1)[-1]
        table = _UNITY_VEC if engine == "unity" else _GODOT_VEC
        v = table.get(const.lower())
        if v is None:
            return None
        v = v[:n]
        return _from_unity_vec(v) if engine == "unity" and n == 3 else v
    if typ == "color":
        m = re.fullmatch(r"new\s*(?:\w+(?:\.\w+)*)?\s*\(([^()]*)\)", t)
        if m:
            vals = [_cs_value(p, "float", engine) for p in m.group(1).split(",") if p.strip()]
            if len(vals) in (3, 4) and None not in vals:
                return tuple(vals) + ((1.0,) if len(vals) == 3 else ())
            return None
        return _COLORS.get(t.rsplit(".", 1)[-1].lower())
    return None


_UNITY_VEC = {"zero": (0.0, 0.0, 0.0), "one": (1.0, 1.0, 1.0), "up": (0.0, 1.0, 0.0), "down": (0.0, -1.0, 0.0),
              "forward": (0.0, 0.0, 1.0), "back": (0.0, 0.0, -1.0), "right": (1.0, 0.0, 0.0), "left": (-1.0, 0.0, 0.0)}
_GODOT_VEC = {"zero": (0.0, 0.0, 0.0), "one": (1.0, 1.0, 1.0), "up": (0.0, 1.0, 0.0), "down": (0.0, -1.0, 0.0),
              "forward": (0.0, 0.0, -1.0), "back": (0.0, 0.0, 1.0), "right": (1.0, 0.0, 0.0), "left": (-1.0, 0.0, 0.0)}
_COLORS = {"white": (1.0, 1.0, 1.0, 1.0), "black": (0.0, 0.0, 0.0, 1.0), "red": (1.0, 0.0, 0.0, 1.0),
           "green": (0.0, 1.0, 0.0, 1.0), "blue": (0.0, 0.0, 1.0, 1.0), "clear": (0.0, 0.0, 0.0, 0.0),
           "transparent": (1.0, 1.0, 1.0, 0.0)}


def _from_unity_vec(v: tuple) -> tuple:
    return (-v[0] or 0.0, v[1], v[2])  # (or 0.0: no -0.0)


def _cs_type_default(typ: str, engine: str):
    return {"float": 0.0, "int": 0, "bool": False, "string": "", "vector2": (0.0, 0.0), "vector3": (0.0, 0.0, 0.0),
            "color": (0.0, 0.0, 0.0, 0.0) if engine == "unity" else (0.0, 0.0, 0.0, 1.0)}.get(typ)


def parse_csharp(text: str, path: str, engine: str) -> ScriptInfo:
    """A C# script: its main class (named like the file, else the first) and its inspector settings.
    Unity: public or [SerializeField] fields, not static/const/readonly/[NonSerialized]. Godot: [Export] members."""
    code = _cs_strip(text)
    stem = Path(path).stem
    classes = list(_cs_class_bodies(code))
    info = ScriptInfo(engine, path)
    main = next((c for c in classes if c[0] == stem), classes[0] if classes else None)
    if main is None:
        return info
    info.cls, info.base = main[0], main[1]
    body = code[main[2]:main[3]]
    # leave nested classes' insides out: _cs_members only looks at depth 0 of this body
    for stmt, block in _cs_members(body):
        attrs, decl = _leading_attributes(stmt)
        if "=>" in decl:  # an expression-bodied property: computed, not stored
            continue
        words = decl.split("=")[0].split()
        if not words or "(" in decl.split("=")[0]:
            continue
        mods = {w for w in words if w in _CS_ACCESS | {"static", "const", "readonly", "new", "override", "virtual",
                                                     "abstract", "partial", "event", "volatile", "unsafe", "extern"}}
        rest = [w for w in words if w not in mods]
        if len(rest) < 2 or mods & {"static", "const", "event"} or "readonly" in mods:
            continue
        if re.search(r"\b(class|struct|enum|interface|delegate|using)\b", decl.split("=")[0]):
            continue
        type_text = rest[0].split(".")[-1].rstrip("?")
        names_part = " ".join(rest[1:])
        default_text = decl.split("=", 1)[1] if "=" in decl else None
        if engine == "unity":
            serialized = ("public" in mods or re.search(r"\bSerializeField\b", attrs)) and not block
            if not serialized or re.search(r"\bNonSerialized\b", attrs):
                continue
        else:
            if not re.search(r"\bExport\w*\b", attrs):
                continue
        typ = _CS_TYPES.get(type_text, "")
        for nm in [n.strip() for n in names_part.split(",") if n.strip()]:
            if not re.fullmatch(r"[A-Za-z_]\w*", nm):
                continue
            if typ:
                d = _cs_value(default_text, typ, engine) if default_text is not None else _cs_type_default(typ, engine)
            else:
                d = None
            info.settings[snake(nm)] = Setting(nm, typ, d, rest[0])
    return info


# ---------------------------------------------------------------- GDScript

_GD_EXPORT = re.compile(r"@export(?!_group|_subgroup|_category)\w*(?:\([^)]*\))?")


def _gd_value(text: str, typ: str):
    t = text.strip()
    if typ in _NUMERIC:
        try:
            v = float(t.replace("_", ""))
        except ValueError:
            return None
        return int(v) if typ == "int" else v
    if typ == "bool":
        return {"true": True, "false": False}.get(t)
    if typ == "string":
        m = re.fullmatch(r"&?(\"(.*)\"|'(.*)')", t, re.S)
        if not m:
            return None
        return _unescape(m.group(2) if m.group(2) is not None else m.group(3))
    if typ in ("vector3", "vector2", "color"):
        m = re.fullmatch(r"(Vector3|Vector2|Color)\s*\(([^()]*)\)", t)
        if m:
            vals = [_gd_value(p, "float") for p in m.group(2).split(",") if p.strip()]
            if None in vals:
                return None
            if typ == "color":
                if not vals:
                    return (0.0, 0.0, 0.0, 1.0)
                return tuple(vals) + ((1.0,) if len(vals) == 3 else ()) if len(vals) in (3, 4) else None
            n = 3 if typ == "vector3" else 2
            return tuple(vals) if len(vals) == n else ((0.0,) * n if not vals else None)
        const = t.rsplit(".", 1)[-1].lower()
        if typ == "color":
            return _COLORS.get(const)
        v = _GODOT_VEC.get(const)
        return v[:3 if typ == "vector3" else 2] if v else None
    return None


def _gd_type_of_value(t: str) -> str:
    t = t.strip()
    if t in ("true", "false"):
        return "bool"
    if re.fullmatch(r"[-+]?\d[\d_]*", t):
        return "int"
    if re.fullmatch(r"[-+]?(\d[\d_]*)?\.\d*([eE][-+]?\d+)?|[-+]?\d+[eE][-+]?\d+", t):
        return "float"
    if re.fullmatch(r"&?(\".*\"|'.*')", t, re.S):
        return "string"
    m = re.match(r"(Vector3|Vector2|Color)\b", t)
    if m:
        return {"Vector3": "vector3", "Vector2": "vector2", "Color": "color"}[m.group(1)]
    return ""


def _gd_type_default(typ: str):
    return {"float": 0.0, "int": 0, "bool": False, "string": "", "vector2": (0.0, 0.0), "vector3": (0.0, 0.0, 0.0),
            "color": (0.0, 0.0, 0.0, 1.0)}.get(typ)


def parse_gdscript(text: str, path: str) -> ScriptInfo:
    info = ScriptInfo("godot", path)
    pending_export = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip() if '"' not in raw and "'" not in raw else raw.rstrip()
        if not line.strip():
            continue
        top = not raw[:1].isspace()
        s = line.strip()
        if top and s.startswith("extends "):
            info.base = s[len("extends "):].strip().strip('"')
            continue
        if top and s.startswith("class_name "):
            info.class_name = s.split()[1]
            if " extends " in s:
                info.base = s.split(" extends ", 1)[1].strip()
            continue
        if not top:
            continue
        exported = pending_export
        m = _GD_EXPORT.match(s)
        while m:
            exported = True
            s = s[m.end():].strip()
            m = _GD_EXPORT.match(s)
        if not s:  # an annotation on its own line, for the next one
            pending_export = exported
            continue
        pending_export = False
        if not exported:
            continue
        m = re.match(r"var\s+([A-Za-z_]\w*)\s*(?::\s*([A-Za-z_][\w\[\]]*)?\s*)?(:?=\s*(.+))?$", s)
        if not m:
            continue
        name, type_text, default_text = m.group(1), m.group(2) or "", m.group(4)
        if default_text is not None:
            default_text = default_text.split("#", 1)[0].strip()
            if default_text.endswith(":"):  # var x = 1: (a setter block follows)
                default_text = default_text[:-1].strip()
        typ = _GD_TYPES.get(type_text, "") if type_text else (_gd_type_of_value(default_text) if default_text else "")
        if typ:
            d = _gd_value(default_text, typ) if default_text is not None else _gd_type_default(typ)
        else:
            d = None
        info.settings[snake(name)] = Setting(name, typ, d, type_text or "Variant")
    info.cls = info.class_name or Path(path).stem
    return info


# ---------------------------------------------------------------- the table


def _key(stem: str) -> str:
    return snake(stem).replace("_", "")


class ScriptTable:
    """Every script pair of the repository, plus the scripts that have no partner (for messages)."""

    def __init__(self) -> None:
        self.pairs: dict[str, ScriptPair] = {}
        self.by_guid: dict[str, ScriptPair] = {}
        self.by_res: dict[str, ScriptPair] = {}
        self.unity_paths: dict[str, str] = {}  # guid -> path, every script under Assets
        self.godot_paths: set[str] = set()
        self.ambiguous: list[str] = []
        self.errors: list[str] = []
        self.godot_infos: dict[str, ScriptInfo] = {}
        self._names: dict[str, str] | None = None

    @classmethod
    def load(cls, cfg: Config) -> "ScriptTable":
        t = cls()
        unity_scripts: dict[str, list[str]] = {}  # name key -> Unity paths
        assets = cfg.unity / "Assets"
        for f in sorted(assets.rglob("*.cs")) if assets.is_dir() else []:
            rel = f.relative_to(cfg.unity).as_posix()
            if "/Editor/" in f"/{rel}":
                continue
            guid = _meta_guid(f)
            if guid:
                t.unity_paths[guid] = rel
            unity_scripts.setdefault(_key(f.stem), []).append(rel)
        godot_scripts: dict[str, list[str]] = {}
        if cfg.godot.is_dir():
            for f in sorted(list(cfg.godot.rglob("*.gd")) + list(cfg.godot.rglob("*.cs"))):
                rel = f.relative_to(cfg.godot).as_posix()
                if rel.startswith((".godot/", "addons/")) or "/.godot/" in rel:
                    continue
                res = "res://" + rel
                t.godot_paths.add(res)
                godot_scripts.setdefault(_key(f.stem), []).append(res)
        chosen: list[tuple[str, str]] = []
        explicit = {u: (g if g.startswith("res://") else "res://" + g.lstrip("/")) for u, g in (cfg.scripts or {}).items()}
        for u, g in explicit.items():
            if not (cfg.unity / u).is_file():
                t.errors.append(f"twinscene.json pairs {u}, which isn't in the Unity project")
            elif not (cfg.godot / g[len("res://"):]).is_file():
                t.errors.append(f"twinscene.json pairs {g}, which isn't in the Godot project")
            else:
                chosen.append((u, g))
        taken_u, taken_g = {u for u, _ in chosen}, {g for _, g in chosen}
        for k, us in unity_scripts.items():
            gs = [g for g in godot_scripts.get(k, []) if g not in taken_g]
            us = [u for u in us if u not in taken_u]
            if not us or not gs:
                continue
            if len(us) == 1 and len(gs) == 1:
                chosen.append((us[0], gs[0]))
            else:
                t.ambiguous.append(f"{', '.join(us)} <-> {', '.join(gs)}: several scripts share a name; pair them in "
                                   "twinscene.json (\"scripts\")")
        for u, g in chosen:
            t._add(cfg, u, g)
        return t

    def _godot_info(self, cfg: Config, res: str) -> ScriptInfo:
        if res not in self.godot_infos:
            f = cfg.godot / res[len("res://"):]
            text = f.read_text(encoding="utf-8", errors="replace")
            self.godot_infos[res] = parse_gdscript(text, res) if res.endswith(".gd") else parse_csharp(text, res, "godot")
        return self.godot_infos[res]

    def _resolve_base(self, cfg: Config, info: ScriptInfo, seen: int = 0) -> str:
        """A Godot script's built-in base class: follows `extends "res://x.gd"` and class_name chains."""
        base = info.base or ("Node" if info.path.endswith(".cs") else "RefCounted")
        if base in GODOT_PARENT or seen > 8:
            return base
        target = base if base.startswith("res://") else self._class_names(cfg).get(base)
        if target is None or not (cfg.godot / target[len("res://"):]).is_file():
            return base
        return self._resolve_base(cfg, self._godot_info(cfg, target), seen + 1)

    def _class_names(self, cfg: Config) -> dict[str, str]:
        """GDScript class_name -> its script (read once)."""
        if self._names is None:
            self._names = {}
            for res in sorted(self.godot_paths):
                if res.endswith(".gd"):
                    try:
                        text = (cfg.godot / res[len("res://"):]).read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    m = re.search(r"^class_name\s+([A-Za-z_]\w*)", text, re.M)
                    if m:
                        self._names.setdefault(m.group(1), res)
        return self._names

    def _add(self, cfg: Config, u: str, g: str) -> None:
        uinfo = parse_csharp((cfg.unity / u).read_text(encoding="utf-8", errors="replace"), u, "unity")
        ginfo = self._godot_info(cfg, g)
        ginfo.base = self._resolve_base(cfg, ginfo)
        name = Path(u).stem
        while name in self.pairs:
            name += "_"
        pair = ScriptPair(name, uinfo, ginfo, _meta_guid(cfg.unity / u) or "")
        for k in sorted(set(uinfo.settings) | set(ginfo.settings)):
            us, gs = uinfo.settings.get(k), ginfo.settings.get(k)
            if us is None:
                pair.only_godot.append(gs.name)
            elif gs is None:
                pair.only_unity.append(us.name)
            elif not us.type or not gs.type:
                pair.unsupported.append(f"{us.name} ({us.type_text} / {gs.type_text})")
            elif us.type == gs.type or (us.type in _NUMERIC and gs.type in _NUMERIC):
                pair.shared[k] = (us, gs)
            else:
                pair.mismatched.append(f"{us.name} ({us.type_text}) / {gs.name} ({gs.type_text})")
        self.pairs[name] = pair
        if pair.guid:
            self.by_guid[pair.guid] = pair
        self.by_res[g] = pair

    def describe(self) -> list[dict]:
        out = []
        for p in self.pairs.values():
            out.append({"name": p.name, "unity": p.unity.path, "godot": p.godot.path, "godot_extends": p.godot.base,
                        "unity_ready": bool(p.guid),
                        "shared": [{"setting": k, "unity": a.name, "godot": b.name, "type": a.type}
                                   for k, (a, b) in p.shared.items()],
                        "only_unity": p.only_unity, "only_godot": p.only_godot, "mismatched": p.mismatched,
                        "unsupported": p.unsupported})
        return out


_GUID = re.compile(r"^guid:\s*([0-9a-f]{32})\s*$", re.M)


def _meta_guid(script: Path) -> str | None:
    try:
        m = _GUID.search(Path(str(script) + ".meta").read_text(encoding="utf-8", errors="replace")[:400])
    except OSError:
        return None
    return m.group(1) if m else None


# ---------------------------------------------------------------- values in each engine's files

def from_unity(raw, typ: str):
    """A serialized field's value in a Unity scene -> TwinScene value."""
    from .unity.yaml import num, vec
    if typ == "float":
        return float(num(raw, 0.0))
    if typ == "int":
        return int(num(raw, 0))
    if typ == "bool":
        return str(raw).strip() in ("1", "true", "True")
    if typ == "string":
        return "" if raw is None else str(raw)
    if typ == "vector3":
        return _from_unity_vec(vec(raw)) if isinstance(raw, dict) else None
    if typ == "vector2":
        return vec(raw, "xy") if isinstance(raw, dict) else None
    if typ == "color":
        return vec(raw, "rgba") if isinstance(raw, dict) else None
    return None


def to_unity(v, typ: str):
    from .unity.yaml import FlowMap
    if typ == "float":
        return float(v)
    if typ == "int":
        return int(round(float(v)))
    if typ == "bool":
        return bool(v)
    if typ == "string":
        return str(v)
    if typ == "vector3":
        x, y, z = _from_unity_vec(tuple(v))
        return FlowMap(x=float(x), y=float(y), z=float(z))
    if typ == "vector2":
        return FlowMap(x=float(v[0]), y=float(v[1]))
    if typ == "color":
        r, g, b, a = (tuple(v) + (1.0,))[:4]
        return FlowMap(r=float(r), g=float(g), b=float(b), a=float(a))
    return None


def from_godot(raw, typ: str):
    from .godot.variant import Call
    if raw is None:
        return None
    if typ in _NUMERIC and isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) if typ == "float" else int(raw)
    if typ == "bool" and isinstance(raw, bool):
        return raw
    if typ == "string" and isinstance(raw, str):
        return str(raw)
    if isinstance(raw, Call) and typ in ("vector3", "vector2", "color"):
        vals = tuple(float(a) for a in raw.args)
        if typ == "color":
            return vals + ((1.0,) if len(vals) == 3 else ())
        return vals
    return None


def to_godot(v, typ: str):
    from .godot.variant import Call
    if typ == "float":
        return float(v)
    if typ == "int":
        return int(round(float(v)))
    if typ == "bool":
        return bool(v)
    if typ == "string":
        return str(v)
    if typ == "vector3":
        return Call("Vector3", tuple(float(c) for c in v))
    if typ == "vector2":
        return Call("Vector2", tuple(float(c) for c in v))
    if typ == "color":
        return Call("Color", tuple(float(c) for c in (tuple(v) + (1.0,))[:4]))
    return None
