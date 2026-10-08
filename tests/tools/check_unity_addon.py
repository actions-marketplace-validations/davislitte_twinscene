"""Builds a TwinScene repository with pending changes, a conflict and a claim, then runs the Unity add-on check
(tests/unity_addon/TwinSceneAddonCheck.cs) inside the real Unity.

    python tests/tools/check_unity_addon.py <Unity.exe> [work folder]
"""

import json
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from twinscene import coords  # noqa: E402
from twinscene.godot import reader as gr, tscn, writer as gw  # noqa: E402
from twinscene.project import GodotProject, UnityProject  # noqa: E402
from twinscene.unity import reader as ur, writer as uw, yaml  # noqa: E402

PROJECTS = ROOT / "tests" / "projects"


def git(cwd, *args, **kw):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, **kw).stdout


def twinscene(ws, *args):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    p = subprocess.run([sys.executable, "-m", "twinscene", "--root", str(ws), *args], env=env, capture_output=True,
                       text=True, encoding="utf-8")
    if p.returncode not in (0, 3):
        raise SystemExit(f"twinscene {' '.join(args)} failed:\n{p.stdout}\n{p.stderr}")
    return p.stdout


def rmtree(p: Path) -> None:
    """shutil.rmtree that also removes git's read-only object files on Windows."""
    def again(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if p.exists():
        shutil.rmtree(p, onerror=again)


def by_path(scene):
    return {scene.path(k): n for k, n in scene.nodes.items()}


def prepare(ws: Path, fresh_settings: bool = False) -> None:
    """fresh_settings: leave out ProjectSettings, so an older Unity than the one that made the test project can
    open it (it makes its own)."""
    rmtree(ws)
    ws.mkdir(parents=True)
    skip = ["Library", "Temp", "Logs", "UserSettings"] + (["ProjectSettings", "packages-lock.json"] if fresh_settings else [])
    shutil.copytree(PROJECTS / "unity_ref", ws / "unity", ignore=shutil.ignore_patterns(*skip))
    shutil.copytree(PROJECTS / "godot_ref", ws / "godot", ignore=shutil.ignore_patterns(".godot"))
    shutil.copy(ROOT / "tests" / "unity_addon" / "TwinSceneAddonCheck.cs", ws / "unity" / "Assets" / "Editor")
    manifest = ws / "unity" / "Packages" / "manifest.json"
    m = json.loads(manifest.read_text())
    if fresh_settings:  # module lists differ between Unity versions: keep only what the test scene needs
        m["dependencies"] = {"com.unity.modules.physics": "1.0.0"}
    m["dependencies"]["com.twinscene.unity"] = "file:" + (ROOT / "addons" / "unity").as_posix()
    manifest.write_text(json.dumps(m, indent=2))
    (ws / "twinscene.json").write_text(json.dumps(
        {"shared": {"unity": "Assets/Shared", "godot": "shared"}, "scenes": [{"name": "Main", "unity": "Assets/Scenes/Reference.unity", "godot": "scenes/main.tscn"}]}))
    (ws / ".gitignore").write_text("unity/Library/\nunity/Temp/\nunity/Logs/\ngodot/.godot/\n")
    remote = ws.parent / (ws.name + "-remote.git")
    rmtree(remote)
    git(ws.parent, "init", "--quiet", "--bare", str(remote))
    git(ws, "init", "--quiet", "-b", "main")
    git(ws, "config", "core.autocrlf", "false")
    git(ws, "config", "user.name", "Ana")
    git(ws, "config", "user.email", "ana@example.com")
    git(ws, "remote", "add", "origin", str(remote))
    twinscene(ws, "sync")

    # Godot side: move Box/Child, resize Box, move Lamp.
    gpath = ws / "godot" / "scenes" / "main.tscn"
    gproj = GodotProject(ws / "godot")
    doc = tscn.load(gpath)
    ed = gw.GodotEditor(doc, gproj, "res://scenes/main.tscn", random.Random(5))
    n = by_path(gr.read(doc, gproj, "res://scenes/main.tscn"))
    for path, changed in (("Box/Child", {"position": (0.0, 3.0, 0.0)}), ("Box", {"mesh.size": (2.0, 1.0, 0.5)}),
                          ("Lamp", {"position": (0.0, 0.0, 5.0)})):
        ed.apply(n[path].key, changed, dict(n[path].fields, **changed))
    ed.finish()
    tscn.save(doc, gpath)

    # Unity side: move Lamp somewhere else (the conflict).
    upath = ws / "unity" / "Assets" / "Scenes" / "Reference.unity"
    uproj = UnityProject(ws / "unity")
    udoc = yaml.load(upath)
    ue = uw.UnityEditor(udoc, uproj, random.Random(6))
    lamp = by_path(ur.read(udoc, uproj))["Lamp"]
    ue.apply(lamp.key, {"position": (5.0, 0.0, 0.0)}, dict(lamp.fields, position=(5.0, 0.0, 0.0)))
    yaml.save(udoc, upath)

    git(ws, "add", "-A")
    git(ws, "commit", "--quiet", "-m", "setup")
    git(ws, "push", "--quiet", "origin", "main")
    # Bo claims Ball.
    git(ws, "config", "user.name", "Bo")
    git(ws, "config", "user.email", "bo@example.com")
    twinscene(ws, "claim", "--scene", "Main", "--unity-path", "Ball")
    git(ws, "config", "user.name", "Ana")
    git(ws, "config", "user.email", "ana@example.com")
    print(twinscene(ws, "status"))


def run_unity(unity: str, ws: Path) -> int:
    log = ws / "unity-check.log"
    env = dict(os.environ, PYTHONPATH=str(ROOT), TWINSCENE_COMMAND=f'"{sys.executable}" -m twinscene')
    subprocess.run([unity, "-batchmode", "-nographics", "-projectPath", str(ws / "unity"),
                    "-executeMethod", "TwinSceneAddonCheck.Run", "-logFile", str(log)], env=env)
    text = log.read_text(encoding="utf-8", errors="replace")
    errors = re.findall(r"^.*error CS\d+.*$", text, re.M)
    for e in sorted(set(errors)):
        print("COMPILE", e)
    for line in re.findall(r"^CHECK .*$", text, re.M):
        print(line)
    warnings = sorted(set(re.findall(r"^.*warning CS\d+.*TwinScene.*$", text, re.M)))
    for w in warnings:
        print("WARNING", w)
    done = re.search(r"^CHECK DONE (\d+) failed", text, re.M)
    if errors or not done:
        print(f"Unity didn't finish the check; see {log}")
        return 1
    return 0 if done.group(1) == "0" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    unity_exe = args[0]
    work = Path(args[1]) if len(args) > 1 else Path(tempfile.gettempdir()) / "twinscene-unity-addon"
    prepare(work, fresh_settings="--fresh-settings" in sys.argv)
    sys.exit(run_unity(unity_exe, work))
