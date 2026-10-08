"""twinscene: keep one game in Unity and Godot at the same time.

    twinscene sync        copy each side's changes to the other (--prefer unity|godot, --resolve KEY:FIELD=SIDE)
    twinscene status      what a sync would do, without changing anything
    twinscene claims      who is working on what
    twinscene claim       "I'm working on this" (--scene NAME and --key, --unity-path or --godot-path)
    twinscene release     give a claim back (or --all of yours)
    twinscene scripts     which Unity and Godot scripts are paired, and which of their settings sync
    twinscene init        write a twinscene.json for this repository

--json prints machine-readable output; the editor add-ons use it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, claims as claims_mod
from .project import Config
from .sync import NeedsPreference, State, scene_pairs, sync


def _find_root(start: Path) -> Path:
    """The folder holding twinscene.json, searching upwards (so the add-ons can run from inside a project)."""
    for d in [start, *start.parents]:
        if (d / "twinscene.json").is_file():
            return d
    return start


def _resolutions(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        try:  # KEY is u:<n> or g:<n>; FIELD is the rest (override fields contain colons too)
            target, side = it.rsplit("=", 1)
            kind, num, fld = target.split(":", 2)
            key = f"{kind}:{num}"
        except ValueError:
            raise SystemExit(f"--resolve takes KEY:FIELD=unity|godot, not {it!r}")
        if side not in ("unity", "godot"):
            raise SystemExit(f"--resolve side must be unity or godot, not {side!r}")
        out[(key, fld)] = side
    return out


def _entry(cfg: Config, scene: str, key: str | None, unity_path: str | None, godot_path: str | None) -> dict:
    """Describe one object for a claim: both keys and both paths, from the last sync."""
    state = State.load(cfg.root / "sync" / f"{scene}.json")
    u2g = {u: g for u, g in state.nodes}
    g2u = {g: u for u, g in state.nodes}
    if key is None:
        snap, path = (state.unity, unity_path) if unity_path is not None else (state.godot, godot_path)
        key = next((k for k in snap.nodes if snap.path(k) == path), None)
        if key is None:
            raise claims_mod.ClaimError(f"{path!r} isn't in the last sync of {scene}; sync first")
    u = key if key.startswith("u:") else g2u.get(key)
    g = key if key.startswith("g:") else u2g.get(key)
    return {"scene": scene, "unity_key": u or "", "godot_key": g or "",
            "unity_path": state.unity.path(u) if u in state.unity.nodes else (unity_path or ""),
            "godot_path": state.godot.path(g) if g in state.godot.nodes else (godot_path or "")}


def _claims_json(root: Path) -> dict:
    try:
        claims, _ = claims_mod.fetch(root)
        return {"me": claims_mod.who(root), "claims": claims}
    except (claims_mod.ClaimError, OSError) as e:
        return {"me": "", "claims": [], "claims_error": str(e)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="twinscene", description="Keep one game in Unity and Godot at the same time.")
    ap.add_argument("--version", action="version", version=f"twinscene {__version__}")
    ap.add_argument("--root", help="repository root (holds twinscene.json); default: found from the current folder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="copy each side's changes to the other")
    s.add_argument("--prefer", choices=["unity", "godot"], help="settle all conflicts in favour of this side")
    s.add_argument("--resolve", action="append", metavar="KEY:FIELD=SIDE",
                   help="settle one conflict, e.g. u:123:position=godot (repeatable)")
    s.add_argument("--dry-run", action="store_true", help="report only; change no files")
    st = sub.add_parser("status", help="what a sync would do")
    for p in (s, st):
        p.add_argument("--report", help="also write the report as Markdown to this file (for pull requests)")
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument("--claims", action="store_true", help="include claims (fetches from the git remote)")
    c = sub.add_parser("claims", help="who is working on what")
    c.add_argument("--json", action="store_true")
    for name, helptext in (("claim", "say you're working on an object"), ("release", "give a claim back")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--scene", help="scene name (as in sync/<scene>.json)")
        p.add_argument("--key", help="the object's key, u:<fileID> or g:<unique_id>")
        p.add_argument("--unity-path", help="or its path in the Unity scene, e.g. Box/Child")
        p.add_argument("--godot-path", help="or its path in the Godot scene")
        p.add_argument("--json", action="store_true")
        if name == "release":
            p.add_argument("--all", action="store_true", help="release all of your claims")
    sc = sub.add_parser("scripts", help="which scripts are paired, and which settings sync")
    sc.add_argument("--json", action="store_true")
    i = sub.add_parser("init", help="write a twinscene.json")
    i.add_argument("--unity", default="unity", help="Unity project folder (default: unity)")
    i.add_argument("--godot", default="godot", help="Godot project folder (default: godot)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a code page without arrows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    as_json = getattr(args, "json", False)

    def fail(msg: str, code: int = 2) -> int:
        if as_json:
            print(json.dumps({"error": msg}))
        else:
            print(msg, file=sys.stderr)
        return code

    root = Path(args.root) if args.root else _find_root(Path.cwd())
    if args.cmd == "init":
        f = root / "twinscene.json"
        if f.exists():
            return fail(f"{f} already exists", 1)
        f.write_text(json.dumps({"unity": args.unity, "godot": args.godot,
                                 "shared": {"unity": "Assets", "godot": ""}}, indent=2) + "\n",
                     encoding="utf-8")
        print(f"wrote {f}. Models, textures, audio and fonts are mirrored between {args.unity}/Assets and "
              f"{args.godot}/ at the same paths (change \"shared\" in it to narrow that).")
        return 0

    cfg = Config.load(root)
    if args.cmd in ("claims", "claim", "release"):
        try:
            if args.cmd == "claims":
                out = _claims_json(root)
            elif args.cmd == "release" and args.all:
                out = {"me": claims_mod.who(root), "claims": claims_mod.release(root)}
            else:
                if not args.scene or not (args.key or args.unity_path or args.godot_path):
                    return fail("give --scene and one of --key, --unity-path, --godot-path")
                entry = _entry(cfg, args.scene, args.key, args.unity_path, args.godot_path)
                fn = claims_mod.claim if args.cmd == "claim" else claims_mod.release
                out = {"me": claims_mod.who(root), "claims": fn(root, entry)}
        except claims_mod.ClaimError as e:
            return fail(str(e))
        if as_json:
            print(json.dumps(out))
        else:
            for cl in out["claims"]:
                print(f"{cl['scene']}: {cl.get('unity_path') or cl.get('godot_path')} - {cl['who']} since {cl['when']}")
            if not out["claims"]:
                print("No claims.")
        return 0

    if args.cmd == "scripts":
        from .scripts import ScriptTable
        table = ScriptTable.load(cfg)
        out = {"pairs": table.describe(), "problems": table.errors + table.ambiguous}
        if as_json:
            print(json.dumps(out))
            return 0
        for p in out["pairs"]:
            print(f"{p['name']}: {p['unity']} <-> {p['godot']} (extends {p['godot_extends']})")
            if not p["unity_ready"]:
                print("    Unity hasn't made this script's .meta yet: open the project in Unity once")
            for st in p["shared"]:
                print(f"    syncs  {st['unity']} <-> {st['godot']}  ({st['type']})")
            for label, key in (("Unity only", "only_unity"), ("Godot only", "only_godot"),
                               ("different kinds of value", "mismatched"), ("not synced (kind of value)", "unsupported")):
                if p[key]:
                    print(f"    {label}: {', '.join(p[key])}")
        for problem in out["problems"]:
            print("!", problem)
        if not out["pairs"]:
            print("No paired scripts. Scripts pair when their file names match (PlayerController.cs and "
                  "player_controller.gd), or list them under \"scripts\" in twinscene.json.")
        return 0

    for p, what in ((cfg.unity / "Assets", "Unity project"), (cfg.godot / "project.godot", "Godot project")):
        if not p.exists():
            return fail(f"no {what} at {p.parent}")
    dry = args.cmd == "status" or getattr(args, "dry_run", False)
    try:
        reports = sync(cfg, prefer=getattr(args, "prefer", None), dry_run=dry,
                       resolve=_resolutions(getattr(args, "resolve", None)))
    except NeedsPreference as e:
        return fail(str(e), 4)
    md = "\n".join(r.markdown() for r in reports)
    if args.report:
        Path(args.report).write_text(md, encoding="utf-8")
    conflicts = any(r.conflicts for r in reports)
    if as_json:
        out = {"root": str(root.resolve()), "dry_run": dry, "assets": reports[0].to_json(),
               "scenes": [r.to_json() for r in reports[1:]]}
        if args.claims:
            out.update(_claims_json(root))
        print(json.dumps(out))
    else:
        print(md)
        if not scene_pairs(cfg):
            print("(no scenes found)")
    return 3 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
