"""Builds a TwinScene repository with pending Unity changes, a conflict and a claim, then runs the Godot plugin check
(tests/godot_addon/addons/twinscene_check) inside the real Godot editor, headless.

    python tests/tools/check_godot_addon.py <godot console exe> [work folder]
"""

import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from check_unity_addon import by_path, git, rmtree, twinscene  # noqa: E402
from twinscene.godot import reader as gr, tscn, writer as gw  # noqa: E402
from twinscene.project import GodotProject, UnityProject  # noqa: E402
from twinscene.unity import reader as ur, writer as uw, yaml  # noqa: E402

PROJECTS = ROOT / "tests" / "projects"
SCENE = "res://scenes/main.tscn"


def prepare(ws: Path) -> None:
    rmtree(ws)
    ws.mkdir(parents=True)
    shutil.copytree(PROJECTS / "unity_ref" / "Assets", ws / "unity" / "Assets",
                    ignore=shutil.ignore_patterns("FromGodot.unity*"))
    shutil.copytree(PROJECTS / "godot_ref", ws / "godot", ignore=shutil.ignore_patterns(".godot"))
    shutil.copytree(ROOT / "addons" / "godot" / "addons" / "twinscene", ws / "godot" / "addons" / "twinscene")
    shutil.copytree(ROOT / "tests" / "godot_addon" / "addons" / "twinscene_check",
                    ws / "godot" / "addons" / "twinscene_check")
    with open(ws / "godot" / "project.godot", "a", encoding="utf-8") as f:
        f.write('\n[editor_plugins]\n\nenabled=PackedStringArray("res://addons/twinscene/plugin.cfg", '
                '"res://addons/twinscene_check/plugin.cfg")\n')
    (ws / "twinscene.json").write_text(
        '{"shared": {"unity": "Assets/Shared", "godot": "shared"}, "scenes": [{"name": "Main", "unity": "Assets/Scenes/Reference.unity", "godot": "scenes/main.tscn"}]}')
    (ws / ".gitignore").write_text("godot/.godot/\n")
    remote = ws.parent / (ws.name + "-remote.git")
    rmtree(remote)
    git(ws.parent, "init", "--quiet", "--bare", str(remote))
    git(ws, "init", "--quiet", "-b", "main")
    git(ws, "config", "core.autocrlf", "false")
    git(ws, "config", "user.name", "Ana")
    git(ws, "config", "user.email", "ana@example.com")
    git(ws, "remote", "add", "origin", str(remote))
    twinscene(ws, "sync")

    # Unity side: move Box/Child, brighten Sun, move Lamp.
    upath = ws / "unity" / "Assets" / "Scenes" / "Reference.unity"
    uproj = UnityProject(ws / "unity")
    udoc = yaml.load(upath)
    ue = uw.UnityEditor(udoc, uproj, random.Random(6))
    n = by_path(ur.read(udoc, uproj))
    for path, changed in (("Box/Child", {"position": (0.0, 3.0, 0.0)}), ("Sun", {"light.energy": 3.0}),
                          ("Lamp", {"position": (-5.0, 0.0, 0.0)})):
        ue.apply(n[path].key, changed, dict(n[path].fields, **changed))
    yaml.save(udoc, upath)

    # Godot side: move Lamp somewhere else (the conflict).
    gpath = ws / "godot" / "scenes" / "main.tscn"
    gproj = GodotProject(ws / "godot")
    doc = tscn.load(gpath)
    ed = gw.GodotEditor(doc, gproj, SCENE, random.Random(5))
    lamp = by_path(gr.read(doc, gproj, SCENE))["Lamp"]
    ed.apply(lamp.key, {"position": (0.0, 0.0, 5.0)}, dict(lamp.fields, position=(0.0, 0.0, 5.0)))
    ed.finish()
    tscn.save(doc, gpath)

    git(ws, "add", "-A")
    git(ws, "commit", "--quiet", "-m", "setup")
    git(ws, "push", "--quiet", "origin", "main")
    git(ws, "config", "user.name", "Bo")
    git(ws, "config", "user.email", "bo@example.com")
    twinscene(ws, "claim", "--scene", "Main", "--godot-path", "Ball")
    git(ws, "config", "user.name", "Ana")
    git(ws, "config", "user.email", "ana@example.com")
    print(twinscene(ws, "status"))


def run_godot(godot: str, ws: Path) -> int:
    env = dict(os.environ, PYTHONPATH=str(ROOT), TWINSCENE_COMMAND=f'"{sys.executable}" -m twinscene',
               TWINSCENE_CHECK="1")
    project = str(ws / "godot")
    subprocess.run([godot, "--headless", "--path", project, "--import"], env=dict(env, TWINSCENE_CHECK="0"),
                   capture_output=True, timeout=300)
    shot = os.environ.get("TWINSCENE_SCREENSHOT")
    window = ["--resolution", "1600x900"] if shot else ["--headless"]  # a screenshot needs a real window
    p = subprocess.run([godot, *window, "--editor", "--path", project], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600)
    out = p.stdout + p.stderr
    (ws / "godot-check.log").write_text(out, encoding="utf-8")
    for line in out.splitlines():
        if line.startswith("CHECK") or re.search(r"SCRIPT ERROR|Parse Error|res://addons/twinscene/", line):
            print(line)
    done = re.search(r"^CHECK DONE (\d+) failed", out, re.M)
    if not done:
        print(f"Godot didn't finish the check; see {ws / 'godot-check.log'}")
        return 1
    return 0 if done.group(1) == "0" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    work = Path(args[1]) if len(args) > 1 else Path(tempfile.gettempdir()) / "twinscene-godot-addon"
    prepare(work)
    sys.exit(run_godot(args[0], work))
