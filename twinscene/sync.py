"""Two-way sync of a Unity scene and a Godot scene.

Each side is compared with its own last-synced snapshot (sync/<scene>.json), so translation details can never look
like edits. Changes made on one side only are copied to the other. A field changed differently on both sides is a
conflict: it is reported and left alone (and keeps being reported) until it is resolved, by making both sides agree
or by syncing with prefer="unity" / prefer="godot".
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import model, models, overrides
from .godot import reader as godot_reader, tscn, writer as godot_writer
from .model import Scene
from .project import Config, GodotProject, UnityProject
from .scripts import ScriptTable
from .unity import reader as unity_reader, writer as unity_writer, yaml

STATE_VERSION = 1
UNITY_MATERIALS = "Assets/TwinScene/Materials"
GODOT_MATERIALS = "res://twinscene/materials"


# ---------------------------------------------------------------- scene pairs


@dataclass
class ScenePair:
    name: str
    unity: str  # inside the Unity project: Assets/Scenes/Main.unity or Assets/Prefabs/Crate.prefab
    godot: str  # res://scenes/Main.tscn
    kind: str = "scene"  # "scene" (a level) or "prefab" (a Unity prefab <-> a Godot scene placed in others)


_GUID_REF = re.compile(r"m_SourcePrefab: \{fileID: \d+, guid: ([0-9a-f]{32})")
_SCENE_REF = re.compile(r'\[ext_resource[^\]]*path="(res://[^"]+\.t?scn)"')


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scene_pairs(cfg: Config) -> list[ScenePair]:
    """From twinscene.json "scenes", or found by name: Assets/**/X.unity or X.prefab <-> **/X.tscn.
    Prefabs come first, innermost first, so a level's prefabs exist on both sides before the level syncs."""
    if cfg.scenes:
        out = []
        for sc in cfg.scenes:
            g = sc["godot"] if sc["godot"].startswith("res://") else "res://" + sc["godot"]
            kind = sc.get("kind") or ("prefab" if sc["unity"].endswith(".prefab") else "scene")
            stem = Path(sc["unity"]).stem
            out.append(ScenePair(sc.get("name") or (f"{stem}.prefab" if kind == "prefab" else stem), sc["unity"], g, kind))
        return _ordered(cfg, _unique_names(out))
    unity_scenes, unity_prefabs = {}, {}
    if (cfg.unity / "Assets").is_dir():
        for pth in sorted((cfg.unity / "Assets").rglob("*.unity")):
            unity_scenes.setdefault(pth.stem.lower(), pth.relative_to(cfg.unity).as_posix())
        for pth in sorted((cfg.unity / "Assets").rglob("*.prefab")):
            unity_prefabs.setdefault(pth.stem.lower(), pth.relative_to(cfg.unity).as_posix())
    godot: dict[str, list[str]] = {}
    placed = set()
    if cfg.godot.is_dir():
        for pth in sorted(cfg.godot.rglob("*.tscn")):
            rel = pth.relative_to(cfg.godot).as_posix()
            if rel.startswith((".godot/", "addons/", cfg.shared_godot + "/")):
                continue
            godot.setdefault(pth.stem.lower(), []).append("res://" + rel)
            placed.update(_SCENE_REF.findall(_text(pth)))

    def take(stem_key: str, kind: str) -> str | None:
        """The Godot scene that pairs with a Unity prefab or scene of this name: the one in the default folder for
        its kind, else one that is (prefab) or isn't (scene) placed in other scenes, else the only candidate when
        Unity doesn't have both kinds with this name."""
        options = godot.get(stem_key, [])
        folder = "res://prefabs/" if kind == "prefab" else "res://scenes/"
        both = stem_key in unity_prefabs and stem_key in unity_scenes
        pick = next((g for g in options if g.lower().startswith(folder) and Path(g).stem.lower() == stem_key), None)
        if pick is None:
            pick = next((g for g in options if (g in placed) == (kind == "prefab")), None)
        if pick is None and len(options) == 1 and not both:
            pick = options[0]
        if pick is not None:
            options.remove(pick)
        return pick

    out = []
    for k, u in sorted(unity_prefabs.items()):  # a prefab and a scene may share a name (3DBall.prefab, 3DBall.unity)
        stem = Path(u).stem
        out.append(ScenePair(f"{stem}.prefab", u, take(k, "prefab") or f"res://prefabs/{stem}.tscn", "prefab"))
    for k, u in sorted(unity_scenes.items()):
        stem = Path(u).stem
        out.append(ScenePair(stem, u, take(k, "scene") or f"res://scenes/{stem}.tscn"))
    for k, options in sorted(godot.items()):
        for g in options:
            stem = Path(g).stem
            if g in placed:  # placed in other scenes: a prefab
                out.append(ScenePair(f"{stem}.prefab", f"Assets/Prefabs/{stem}.prefab", g, "prefab"))
            else:
                out.append(ScenePair(stem, f"Assets/Scenes/{stem}.unity", g))
    return _ordered(cfg, _unique_names(out))


def _unique_names(pairs: list[ScenePair]) -> list[ScenePair]:
    """Each pair keeps its state in sync/<name>.json, so names must differ: two Crate.prefab files in different
    folders are told apart by their path."""
    count: dict[str, int] = {}
    for p in pairs:
        count[p.name.lower()] = count.get(p.name.lower(), 0) + 1
    for p in pairs:
        if count[p.name.lower()] > 1:
            rel = p.unity[len("Assets/"):] if p.unity.startswith("Assets/") else p.unity
            p.name = re.sub(r"[^\w.\- ]", "~", rel.rsplit(".", 1)[0]) + (".prefab" if p.kind == "prefab" else "")
    return pairs


def _ordered(cfg: Config, pairs: list[ScenePair]) -> list[ScenePair]:
    """Prefabs before scenes, and a prefab after the prefabs it contains."""
    uproj = UnityProject(cfg.unity, cfg.shared_unity)
    prefabs = [p for p in pairs if p.kind == "prefab"]
    by_unity = {p.unity: p for p in prefabs}
    by_godot = {p.godot: p for p in prefabs}

    def deps(p: ScenePair) -> list[ScenePair]:
        out = []
        for guid in _GUID_REF.findall(_text(cfg.unity / p.unity)):
            d = by_unity.get(uproj.path_of(guid) or "")
            if d is not None and d is not p:
                out.append(d)
        for res in _SCENE_REF.findall(_text(cfg.godot / p.godot[len("res://"):])):
            d = by_godot.get(res)
            if d is not None and d is not p:
                out.append(d)
        return out

    done: list[ScenePair] = []
    visiting: set[int] = set()

    def visit(p: ScenePair) -> None:
        if p in done or id(p) in visiting:
            return
        visiting.add(id(p))
        for d in deps(p):
            visit(d)
        done.append(p)

    for p in prefabs:
        visit(p)
    return done + [p for p in pairs if p.kind != "prefab"]


class MaterialTable:
    """Which Unity material is which Godot material, for the whole project (sync/materials.json)."""

    def __init__(self, path: Path, rows: list):
        self.path = path
        self.pairs = Pairs(rows)

    @classmethod
    def load(cls, cfg: Config) -> "MaterialTable":
        path = cfg.root / "sync" / "materials.json"
        rows = json.loads(path.read_text(encoding="utf-8")).get("materials", []) if path.is_file() else []
        return cls(path, rows)

    def absorb(self, rows: list) -> None:
        """Pairs recorded per scene by older versions."""
        for u, g in rows:
            if u not in self.pairs.u2g and g not in self.pairs.g2u:
                self.pairs.add(u, g)

    def save(self) -> None:
        rows = sorted(self.pairs.rows())
        if not rows and not self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('{\n "materials": ' + _rows(rows) + "\n}\n", encoding="utf-8", newline="\n")


class PrefabMap:
    """Which Unity prefab is which Godot scene, for translating placed prefabs."""

    def __init__(self, cfg: Config, pairs: list[ScenePair]):
        uproj = UnityProject(cfg.unity, cfg.shared_unity)
        gproj = GodotProject(cfg.godot, cfg.shared_godot)
        rows = [[uproj.ref(p.unity), gproj.ref(p.godot), p.name] for p in pairs if p.kind == "prefab"]
        self.u2g = {u: g for u, g, _ in rows}
        self.g2u = {g: u for u, g, _ in rows}
        self.name_u = {u: n for u, _, n in rows}
        self.name_g = {g: n for _, g, n in rows}
        self.cfg = cfg
        self._inner: dict[str, "Pairs"] = {}

    def inner(self, pair_name: str) -> "Pairs":
        """The ID table of a prefab pair: its objects' Unity keys <-> Godot keys (from its last sync)."""
        if pair_name not in self._inner:
            self._inner[pair_name] = Pairs(State.load(self.cfg.root / "sync" / f"{pair_name}.json").nodes)
        return self._inner[pair_name]

    def override_u2g(self, field: str, prefab_u: str | None) -> str | None:
        """'o:u:1:x' on a placed Unity prefab -> 'o:g:2:x' (None if the inner object isn't paired)."""
        p = overrides.parse(field)
        if p is None:
            return field
        name = self.name_u.get(prefab_u or "")
        g = self.inner(name).u2g.get(p[0]) if name else None
        return overrides.name(g, p[1]) if g else None

    def override_g2u(self, field: str, prefab_g: str | None) -> str | None:
        p = overrides.parse(field)
        if p is None:
            return field
        name = self.name_g.get(prefab_g or "")
        u = self.inner(name).g2u.get(p[0]) if name else None
        return overrides.name(u, p[1]) if u else None


# ---------------------------------------------------------------- state


@dataclass
class State:
    path: Path
    nodes: list[list[str]] = field(default_factory=list)  # [unity key, godot key]
    materials: list[list[str]] = field(default_factory=list)
    unity: Scene = field(default_factory=Scene)
    godot: Scene = field(default_factory=Scene)

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.is_file():
            return cls(path)
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, d.get("nodes", []), d.get("materials", []),
                   Scene.from_json(d.get("unity", {})), Scene.from_json(d.get("godot", {})))

    def save(self) -> None:
        """One line per object, so git diffs (and merge conflicts) of the state file stay readable."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = ["{", f' "version": {STATE_VERSION},',
               f' "nodes": {_rows(sorted(self.nodes))},',
               f' "materials": {_rows(sorted(self.materials))},']
        for side in ("unity", "godot"):
            snap = getattr(self, side).to_json()
            out.append(f' "{side}": {{')
            for kind in ("nodes", "materials"):
                lines = [f"   {json.dumps(k)}: {json.dumps(v, sort_keys=True)}" for k, v in sorted(snap[kind].items())]
                body = "\n" + ",\n".join(lines) + "\n  " if lines else ""
                out.append(f'  "{kind}": {{{body}}}' + ("," if kind == "nodes" else ""))
            out.append(" }" + ("," if side == "unity" else ""))
        out.append("}")
        self.path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def _rows(rows: list) -> str:
    if not rows:
        return "[]"
    return "[\n" + ",\n".join("  " + json.dumps(r) for r in rows) + "\n ]"


class Pairs:
    """A two-way table of paired keys."""

    def __init__(self, rows: list[list[str]]):
        self.u2g = {u: g for u, g in rows}
        self.g2u = {g: u for u, g in rows}

    def add(self, u: str, g: str) -> None:
        self.drop_u(u)
        self.drop_g(g)
        self.u2g[u], self.g2u[g] = g, u

    def drop_u(self, u: str) -> None:
        g = self.u2g.pop(u, None)
        if g is not None:
            self.g2u.pop(g, None)

    def drop_g(self, g: str) -> None:
        u = self.g2u.pop(g, None)
        if u is not None:
            self.u2g.pop(u, None)

    def rows(self) -> list[list[str]]:
        return [[u, g] for u, g in self.u2g.items()]


# ---------------------------------------------------------------- diff


@dataclass
class Delta:
    created: list[str] = field(default_factory=list)  # parents before children
    deleted: list[str] = field(default_factory=list)
    changed: dict[str, set[str]] = field(default_factory=dict)
    reordered: set = field(default_factory=set)  # parent keys (None = scene root) whose child order changed
    materials: dict[str, set[str]] = field(default_factory=dict)


def equal(name: str, a, b) -> bool:
    if name == "rotation" and a is not None and b is not None:
        return model.same_rotation(a, b)
    return model.same(a, b)


def diff(base: Scene, now: Scene) -> Delta:
    d = Delta()
    for n in now.walk():
        if n.key not in base.nodes:
            d.created.append(n.key)
            d.reordered.add(n.parent)
    d.deleted = [k for k in base.nodes if k not in now.nodes]
    for k, n in now.nodes.items():
        b = base.nodes.get(k)
        if b is None:
            continue
        names = (set(n.fields) | set(b.fields)) - {"index"}
        ch = {f for f in names if not equal(f, b.fields.get(f), n.fields.get(f))}
        if ch:
            d.changed[k] = ch
    parents = {n.parent for n in now.nodes.values()} | {None}
    for p in parents:
        now_kids = [c.key for c in now.children(p) if c.key in base.nodes and base.nodes[c.key].parent == p]
        base_kids = [c.key for c in base.children(p) if c.key in now.nodes and now.nodes[c.key].parent == p]
        if now_kids != base_kids:
            d.reordered.add(p)
    for k, m in now.materials.items():
        b = base.materials.get(k)
        if b is not None:
            ch = {f for f in set(m.fields) | set(b.fields) if not model.same(m.fields.get(f), b.fields.get(f))}
            if ch:
                d.materials[k] = ch
    return d


# ---------------------------------------------------------------- report


@dataclass
class Conflict:
    path: str
    field: str
    unity: object
    godot: object
    unity_key: str = ""
    godot_key: str = ""
    godot_path: str = ""

    def to_json(self) -> dict:
        return {"path": self.path, "godot_path": self.godot_path or self.path, "field": self.field,
                "unity": _plain(self.unity), "godot": _plain(self.godot),
                "unity_key": self.unity_key, "godot_key": self.godot_key}


@dataclass
class Report:
    scene: str
    to_godot: list[str] = field(default_factory=list)
    to_unity: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    unity_scene: str = ""
    godot_scene: str = ""
    changes: list[dict] = field(default_factory=list)  # one per object change, for the editor add-ons

    def to_json(self) -> dict:
        return {"scene": self.scene, "unity_scene": self.unity_scene, "godot_scene": self.godot_scene,
                "changes": self.changes, "conflicts": [c.to_json() for c in self.conflicts],
                "to_godot": self.to_godot, "to_unity": self.to_unity, "warnings": self.warnings}

    @property
    def empty(self) -> bool:
        return not (self.to_godot or self.to_unity or self.conflicts)

    def markdown(self) -> str:
        lines = [f"### {self.scene}"]
        if self.empty and not self.warnings:
            return lines[0] + "\n\nNo changes.\n"
        for title, items in (("Unity → Godot", self.to_godot), ("Godot → Unity", self.to_unity)):
            if items:
                lines += ["", f"**{title}**", ""] + [f"- {i}" for i in items]
        if self.conflicts:
            lines += ["", "**Conflicts** (changed differently on both sides; left as they are)", "",
                      "| Object | Field | Unity | Godot |", "|---|---|---|---|"]
            for c in self.conflicts:
                lines.append(f"| {c.path} | {c.field} | {_short(c.unity)} | {_short(c.godot)} |")
            lines += ["", "Make both sides agree, or re-run with `--prefer unity` or `--prefer godot`."]
        if self.warnings:
            lines += ["", "<details><summary>Notes</summary>", ""] + [f"- {w}" for w in _grouped(self.warnings)] +                      ["", "</details>"]
        return "\n".join(lines) + "\n"


def _grouped(warnings: list[str]) -> list[str]:
    """ "Godot: Ball: x" and "Godot: Cube: x" -> "Godot: x (2 objects: Ball, Cube)". """
    groups: dict[tuple, list[str]] = {}
    for w in dict.fromkeys(warnings):  # (no exact repeats)
        parts = w.split(": ", 2)
        key, who = ((parts[0], parts[2]), parts[1]) if len(parts) == 3 else ((w,), None)
        groups.setdefault(key, []).append(who)
    out = []
    for key, names in groups.items():
        if len(key) == 1 or names == [None]:
            out.append(key[0])
        elif len(names) == 1:
            out.append(f"{key[0]}: {names[0]}: {key[1]}")
        else:
            shown = ", ".join(names[:6]) + (", ..." if len(names) > 6 else "")
            out.append(f"{key[0]}: {key[1]} ({len(names)} objects: {shown})")
    return out


def _plain(v):
    return list(v) if isinstance(v, tuple) else v


def _short(v) -> str:
    if isinstance(v, tuple):
        return "(" + ", ".join(f"{c:.3g}" if isinstance(c, float) else str(c) for c in v) + ")"
    if v is None:
        return "*(none)*"
    return str(v)


# ---------------------------------------------------------------- materials


class Materials:
    """Pairs materials across engines, making the missing side when a synced object needs it."""

    def __init__(self, pairs: "Pairs", U: Scene, G: Scene, uproj: UnityProject, gproj: GodotProject, gdoc,
                 scene_res: str, rng: random.Random, report: Report, dry_run: bool = False):
        self.dry_run = dry_run
        self.pairs = pairs  # shared by every scene, so one Unity material has one Godot twin
        self.U, self.G = U, G
        self.uproj, self.gproj = uproj, gproj
        self.gdoc, self.scene_res = gdoc, scene_res
        self.rng = rng
        self.report = report

    def to_godot(self, ukey: str) -> str | None:
        if not ukey or ukey.startswith("unity:builtin:"):
            return None
        if ukey in self.pairs.u2g:
            return self.pairs.u2g[ukey]
        m = self.U.materials.get(ukey)
        if m is None:
            self.report.warnings.append(f"material {ukey} couldn't be read; Godot uses its default")
            return None
        res = self._free(GODOT_MATERIALS, m.fields.get("name") or "Material", ".tres",
                         lambda p: (self.gproj.path / p[6:]).exists())
        target = self.gproj.path / res[6:]
        if not self.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            godot_writer.write_material(target, m.fields)
        gkey = self.gproj.ref(res)
        self.pairs.add(ukey, gkey)
        self.report.files.append(str(target))
        self.report.to_godot.append(f"new material {res}")
        return gkey

    def to_unity(self, gkey: str) -> str | None:
        if not gkey:
            return None
        if gkey in self.pairs.g2u:
            return self.pairs.g2u[gkey]
        m = self.G.materials.get(gkey)
        if m is None:
            self.report.warnings.append(f"material {gkey} couldn't be read; Unity uses its default")
            return None
        path = self._free(UNITY_MATERIALS, m.fields.get("name") or "Material", ".mat",
                          lambda p: (self.uproj.path / p).exists())
        if not self.dry_run:
            unity_writer.write_material(self.uproj, path, m.fields, self.rng)
        ukey = self.uproj.ref(path)
        self.pairs.add(ukey, gkey)
        self.report.files.append(str(self.uproj.path / path))
        self.report.to_unity.append(f"new material {path}")
        return ukey

    @staticmethod
    def _free(folder: str, name: str, ext: str, exists) -> str:
        base = re.sub(r"[^\w\- ]", "_", name).strip() or "Material"
        p, n = f"{folder}/{base}{ext}", 2
        while exists(p):
            p, n = f"{folder}/{base} {n}{ext}", n + 1
        return p

    def push_values(self, dU: Delta, dG: Delta, prefer: str | None) -> None:
        """Material colour/metal/roughness edits go across to the paired material."""
        for ukey, fields in dU.materials.items():
            gkey = self.pairs.u2g.get(ukey)
            if gkey is None:
                continue
            fields = fields - dG.materials.get(gkey, set()) if prefer != "unity" else fields
            vals = {f: self.U.materials[ukey].fields.get(f) for f in fields if f != "albedo_texture"}
            if vals and (self.dry_run or self._write_godot(gkey, vals)):
                self.report.to_godot.append(f"material {gkey}: {', '.join(sorted(vals))}")
        for gkey, fields in dG.materials.items():
            ukey = self.pairs.g2u.get(gkey)
            if ukey is None:
                continue
            fields = fields - dU.materials.get(ukey, set()) if prefer != "godot" else fields
            vals = {f: self.G.materials[gkey].fields.get(f) for f in fields if f != "albedo_texture"}
            path = self.uproj.asset_path(ukey)
            if vals and path and path.endswith(".mat") and (self.uproj.path / path).is_file():
                if not self.dry_run:
                    unity_writer.update_material(self.uproj, path, vals)
                self.report.files.append(str(self.uproj.path / path))
                self.report.to_unity.append(f"material {path}: {', '.join(sorted(vals))}")

    def _write_godot(self, gkey: str, vals: dict) -> bool:
        if "#" in gkey:
            path, rid = gkey[6:].split("#", 1)
            sec = self.gdoc.sub_resource(rid) if path == self.scene_res else None
            if sec is None:
                return False
            godot_writer.update_material(self.gdoc, sec, vals)
            return True
        res = self.gproj.res_path(gkey)
        if not res or not res.endswith(".tres"):
            return False
        f = self.gproj.path / res[6:]
        doc = tscn.load(f)
        godot_writer.update_material(doc, doc.by_tag("resource")[0], vals)
        tscn.save(doc, f)
        self.report.files.append(str(f))
        return True


# ---------------------------------------------------------------- the sync


class NeedsPreference(RuntimeError):
    pass


def _first_link(state: State, U: Scene, G: Scene, ids: Pairs, prefer: str | None, report: Report) -> None:
    """The first sync of two scenes that both have objects: pair objects with the same path. Where the paired
    objects differ, the preferred side wins; objects on one side only are copied to the other."""
    upaths = {U.path(k): k for k in U.nodes}
    gpaths = {G.path(k): k for k in G.nodes}
    common = sorted(set(upaths) & set(gpaths))
    if not common:
        return
    if prefer is None:
        raise NeedsPreference(
            f"{report.scene}: both scenes already have objects ({len(common)} with the same path). For this first "
            "sync, choose which side wins where they differ: --prefer unity or --prefer godot.")
    for p in common:
        ids.add(upaths[p], gpaths[p])
    # The preferred side looks as if every field of a paired object changed; the other side looks unchanged.
    win, lose = (U, G) if prefer == "unity" else (G, U)
    win_keys = set(ids.u2g) if prefer == "unity" else set(ids.g2u)
    lose_keys = set(ids.g2u) if prefer == "unity" else set(ids.u2g)
    base_win = Scene(nodes={k: model.Node(k, {}) for k in win_keys})
    base_lose = Scene(nodes={k: model.Node(k, dict(lose.nodes[k].fields)) for k in lose_keys})
    if prefer == "unity":
        state.unity, state.godot = base_win, base_lose
    else:
        state.godot, state.unity = base_win, base_lose
    report.warnings.append(f"first sync: paired {len(common)} objects by path; {prefer} wins where they differ")


def sync_pair(cfg: Config, pair: ScenePair, prefer: str | None = None, dry_run: bool = False,
              rng: random.Random | None = None, resolve: dict | None = None,
              prefabs: PrefabMap | None = None, materials: "MaterialTable | None" = None,
              scripts: "ScriptTable | None" = None) -> Report:
    """resolve: {(unity or godot key, field): "unity" | "godot"} settles single conflicts."""
    rng = rng or random.Random()
    resolve = resolve or {}
    report = Report(pair.name, unity_scene=pair.unity, godot_scene=pair.godot)
    notes: list[tuple] = []  # (direction, kind, unity key, godot key, fields)
    uproj = UnityProject(cfg.unity, cfg.shared_unity)
    gproj = GodotProject(cfg.godot, cfg.shared_godot)
    uproj.scripts = gproj.scripts = scripts or ScriptTable.load(cfg)  # read by the readers and writers
    upath = cfg.unity / pair.unity
    gpath = cfg.godot / pair.godot[len("res://"):]
    state = State.load(cfg.root / "sync" / f"{pair.name}.json")

    is_prefab = pair.kind == "prefab"
    prefabs = prefabs or PrefabMap(cfg, [pair])
    udoc = yaml.load(upath) if upath.is_file() else (unity_writer.new_prefab() if is_prefab else unity_writer.new_scene())
    gdoc = tscn.load(gpath) if gpath.is_file() else godot_writer.new_scene()
    U = unity_reader.read(udoc, uproj)
    G = godot_reader.read(gdoc, gproj, pair.godot, prefab=is_prefab)
    report.warnings += [f"Unity: {w}" for w in U.warnings] + [f"Godot: {w}" for w in G.warnings]
    ids = Pairs(state.nodes)
    if not state.path.is_file() and U.nodes and G.nodes:
        _first_link(state, U, G, ids, prefer, report)
    dU, dG = diff(state.unity, U), diff(state.godot, G)
    own_table = materials is None
    materials = materials or MaterialTable.load(cfg)
    materials.absorb(state.materials)
    mats = Materials(materials.pairs, U, G, uproj, gproj, gdoc, pair.godot, rng, report, dry_run)

    def to_g(f: dict) -> dict:
        out = {}
        for k, v in f.items():  # edits inside a placed prefab: name the inner object as Godot does
            if k.startswith("o:"):
                k2 = prefabs.override_u2g(k, f.get("prefab"))
                if k2 is None:
                    report.warnings.append(f"{f.get('name')}: an edit inside it ({k}) has no Godot counterpart yet")
                    continue
                if k.endswith(":materials"):
                    v = [g or "" for g in (mats.to_godot(m) for m in v or [])]
                out[k2] = v
            else:
                out[k] = v
        out.pop("index", None)
        if out.get("parent") is not None:
            out["parent"] = ids.u2g.get(out["parent"])
        if "materials" in out:
            out["materials"] = [g for g in (mats.to_godot(m) for m in out["materials"]) if g]
        if out.get("prefab"):
            placed = prefabs.u2g.get(out["prefab"])
            if placed is None:
                report.warnings.append(f"{f.get('name')}: placed prefab {out['prefab']} has no Godot scene paired with it")
                out.pop("prefab")
            else:
                out["prefab"] = placed
        return out

    def to_u(f: dict) -> dict:
        out = {}
        for k, v in f.items():
            if k.startswith("o:"):
                k2 = prefabs.override_g2u(k, f.get("prefab"))
                if k2 is None:
                    report.warnings.append(f"{f.get('name')}: an edit inside it ({k}) has no Unity counterpart yet")
                    continue
                if k.endswith(":materials"):
                    v = [u or "" for u in (mats.to_unity(m) for m in v or [])]
                out[k2] = v
            else:
                out[k] = v
        out.pop("index", None)
        if out.get("parent") is not None:
            out["parent"] = ids.g2u.get(out["parent"])
        if "materials" in out:
            out["materials"] = [u for u in (mats.to_unity(m) for m in out["materials"]) if u]
        if out.get("prefab"):
            placed = prefabs.g2u.get(out["prefab"])
            if placed is None:
                report.warnings.append(f"{f.get('name')}: placed scene {out['prefab']} has no Unity prefab paired with it")
                out.pop("prefab")
            else:
                out["prefab"] = placed
        return out

    # -- conflicts
    conflicts: list[Conflict] = []
    conflict_names: dict[tuple, str] = {}  # (unity key, unity field) -> the Godot field name
    skip_u: dict[str, set] = {}  # unity key -> fields not to send to Godot
    skip_g: dict[str, set] = {}
    for u, g in list(ids.u2g.items()):
        fu, fg = dU.changed.get(u, set()), dG.changed.get(g, set())
        u_prefab = (U.nodes[u].fields.get("prefab") if u in U.nodes else None) or \
                   (state.unity.nodes[u].fields.get("prefab") if u in state.unity.nodes else None)
        as_g = {fname: prefabs.override_u2g(fname, u_prefab) for fname in fu}
        for f_u, f in as_g.items():
            if f is None or f not in fg:
                continue
            uv, gv = U.nodes[u].fields.get(f_u), G.nodes[g].fields.get(f)
            if f.startswith("o:") and f.endswith(":materials"):
                uv = [mats.pairs.u2g.get(m, "?" + m) for m in uv or []]
            if f == "parent":
                uv = ids.u2g.get(uv) if uv is not None else None
            if f == "prefab":
                uv = prefabs.u2g.get(uv, "?")
            if f == "materials":  # compare through the material pairing (an unpaired one never matches)
                uv = [mats.pairs.u2g.get(m, "?" + m) for m in uv or [] if not m.startswith("unity:builtin:")]
                gv = list(gv or [])
            fkey = f.rsplit(":", 1)[1] if f.startswith("o:") else f
            if equal(fkey, uv, gv):
                skip_u.setdefault(u, set()).add(f_u)
                skip_g.setdefault(g, set()).add(f)
                continue
            side = resolve.get((u, f_u)) or resolve.get((g, f)) or prefer
            if side == "unity":
                skip_g.setdefault(g, set()).add(f)
            elif side == "godot":
                skip_u.setdefault(u, set()).add(f_u)
            else:
                conflicts.append(Conflict(U.path(u), f_u, U.nodes[u].fields.get(f_u), G.nodes[g].fields.get(f), u, g,
                                          G.path(g)))
                conflict_names[(u, f_u)] = f
                skip_u.setdefault(u, set()).add(f_u)
                skip_g.setdefault(g, set()).add(f)
    keep_u, keep_g = set(), set()  # deleted on one side but edited on the other: the edit wins, unless preferred
    for u in dU.deleted:
        g = ids.u2g.get(u)
        if g is not None and g in dG.changed and prefer != "unity":
            keep_g.add(g)
            report.warnings.append(f"{state.godot.path(g) if g in state.godot.nodes else g}: deleted in Unity but "
                                   "edited in Godot; kept, and put back in Unity")
    for g in dG.deleted:
        u = ids.g2u.get(g)
        if u is not None and u in dU.changed and prefer != "godot":
            keep_u.add(u)
            report.warnings.append(f"{state.unity.path(u) if u in state.unity.nodes else u}: deleted in Godot but "
                                   "edited in Unity; kept, and put back in Godot")

    ue = unity_writer.UnityEditor(udoc, uproj, rng, prefab=is_prefab)
    ge = godot_writer.GodotEditor(gdoc, gproj, pair.godot, rng, prefab=is_prefab)

    # -- Unity -> Godot
    for u in dU.deleted:
        g = ids.u2g.get(u)
        if g is not None and g not in keep_g and g in ge.by_key:
            report.to_godot.append(f"deleted {state.unity.path(u)}")
            notes.append(("to_godot", "deleted", u, g, []))
            ge.delete(g)
        if g not in keep_g:
            ids.drop_u(u)
    for u in dU.created + [k for k in keep_u if k in U.nodes]:
        n = U.nodes[u]
        if u in ids.u2g and u not in keep_u:
            continue
        f = to_g(n.fields)
        if n.parent is not None and f.get("parent") is None:
            report.warnings.append(f"{U.path(u)}: its parent isn't in Godot; added at the top")
        g = ge.create(f, f.get("parent"))
        ids.add(u, g)
        report.to_godot.append(f"added {U.path(u)}")
        notes.append(("to_godot", "added", u, g, []))
    for u, fields in dU.changed.items():
        g = ids.u2g.get(u)
        if g is None or g not in ge.by_key:
            continue
        send = fields - skip_u.get(u, set())
        if not send:
            continue
        full = to_g(U.nodes[u].fields)
        names = {prefabs.override_u2g(f, U.nodes[u].fields.get("prefab") or
                                      (state.unity.nodes[u].fields.get("prefab") if u in state.unity.nodes else None))
                 for f in send}
        ge.apply(g, {f: full.get(f) for f in names if f}, full)
        report.to_godot.append(f"{U.path(u)}: {', '.join(sorted(send))}")
        notes.append(("to_godot", "changed", u, g, sorted(send)))

    # -- Godot -> Unity
    for g in dG.deleted:
        u = ids.g2u.get(g)
        if u is not None and u not in keep_u and ue.obj(u) is not None:
            report.to_unity.append(f"deleted {state.godot.path(g)}")
            notes.append(("to_unity", "deleted", u, g, []))
            ue.delete(u)
        if u not in keep_u:
            ids.drop_g(g)
    for g in dG.created + [k for k in keep_g if k in G.nodes]:
        n = G.nodes[g]
        if g in ids.g2u and g not in keep_g:
            continue
        f = to_u(n.fields)
        if n.parent is not None and f.get("parent") is None:
            report.warnings.append(f"{G.path(g)}: its parent isn't in Unity; added at the top")
        u = ue.create(f, f.get("parent"))
        ids.add(u, g)
        report.to_unity.append(f"added {G.path(g)}")
        notes.append(("to_unity", "added", u, g, []))
    for g, fields in dG.changed.items():
        u = ids.g2u.get(g)
        if u is None or ue.obj(u) is None:
            continue
        send = fields - skip_g.get(g, set())
        if not send:
            continue
        full = to_u(G.nodes[g].fields)
        names = {prefabs.override_g2u(f, G.nodes[g].fields.get("prefab") or
                                      (state.godot.nodes[g].fields.get("prefab") if g in state.godot.nodes else None))
                 for f in send}
        ue.apply(u, {f: full.get(f) for f in names if f}, full)
        report.to_unity.append(f"{G.path(g)}: {', '.join(sorted(send))}")
        notes.append(("to_unity", "changed", u, g, sorted(send)))

    # -- sibling order
    for p in dU.reordered:
        gp = None if p is None else ids.u2g.get(p)
        if p is not None and gp is None:
            continue
        ge.order_children(gp, [ids.u2g[c.key] for c in U.children(p) if c.key in ids.u2g])
    for p in dG.reordered:
        up = None if p is None else ids.g2u.get(p)
        if p is not None and up is None:
            continue
        ue.order_children(up, [ids.g2u[c.key] for c in G.children(p) if c.key in ids.g2u])

    mats.push_values(dU, dG, prefer)
    ge.finish()
    report.warnings += [f"Unity: {w}" for w in ue.warnings] + [f"Godot: {w}" for w in ge.warnings]
    report.conflicts = conflicts

    # -- save, then snapshot what the files now say
    if not dry_run and ge.extract_requests:  # Godot saves these model meshes as their own files when it imports
        for model_res, mesh_name in dict.fromkeys(ge.extract_requests):
            models.ensure_extracted(gproj.path, model_res, mesh_name)
        gproj.forget_extracted()  # so the snapshot below sees them
    udoc = yaml.loads(udoc.dump())
    gdoc = tscn.loads(gdoc.dump())
    U2 = unity_reader.read(udoc, uproj)
    G2 = godot_reader.read(gdoc, gproj, pair.godot, prefab=is_prefab)
    def where(now: Scene, base: Scene, key: str) -> str:
        for sc in (now, base):
            if key in sc.nodes:
                return sc.path(key)
        return ""

    for direction, kind, u, g, fields in notes:
        report.changes.append({"direction": direction, "kind": kind, "fields": fields, "unity_key": u or "",
                               "godot_key": g or "", "unity_path": where(U2, state.unity, u) if u else "",
                               "godot_path": where(G2, state.godot, g) if g else ""})
    for c in conflicts:  # keep the old value in both snapshots so the conflict shows up again next time
        gfield = conflict_names.get((c.unity_key, c.field), c.field)
        for snap, base, key, fname in ((U2, state.unity, c.unity_key, c.field), (G2, state.godot, c.godot_key, gfield)):
            if key in snap.nodes and key in base.nodes:
                old = base.nodes[key].fields.get(fname)
                if old is None:
                    snap.nodes[key].fields.pop(fname, None)
                else:
                    snap.nodes[key].fields[fname] = old
    if not dry_run:
        if report.to_unity or not upath.is_file():
            new = not upath.is_file()
            unity_writer.make_folders(uproj, upath.parent, rng)
            yaml.save(udoc, upath)
            if new and is_prefab:
                uproj.register(pair.unity, unity_writer.write_prefab_meta(upath, rng))
            elif new:
                unity_writer.write_scene_meta(upath, rng)
            report.files.append(str(upath))
        if report.to_godot or not gpath.is_file():
            gpath.parent.mkdir(parents=True, exist_ok=True)
            tscn.save(gdoc, gpath)
            report.files.append(str(gpath))
        state.nodes = [r for r in ids.rows() if r[0] in U2.nodes and r[1] in G2.nodes]
        state.materials = []  # (kept project-wide in sync/materials.json)
        state.unity, state.godot = U2, G2
        state.save()
        if own_table:
            materials.save()
    return report


def sync(cfg: Config, prefer: str | None = None, dry_run: bool = False, rng: random.Random | None = None,
         resolve: dict | None = None) -> list[Report]:
    from .assets import mirror_shared
    reports = [mirror_shared(cfg, prefer=prefer, dry_run=dry_run, rng=rng)]
    pairs = scene_pairs(cfg)
    prefabs = PrefabMap(cfg, pairs)
    materials = MaterialTable.load(cfg)
    scripts = ScriptTable.load(cfg)
    reports[0].warnings += scripts.errors + scripts.ambiguous
    for pair in pairs:
        reports.append(sync_pair(cfg, pair, prefer, dry_run, rng, resolve, prefabs, materials, scripts))
    if not dry_run:
        materials.save()
    return reports
