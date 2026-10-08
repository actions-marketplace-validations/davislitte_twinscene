"""Prefabs, checked inside both real editors.

Syncs the prefab fixtures both ways (Unity's Crate/Stack/Prefabs.unity into Godot, Godot's crate/stack/prefabs.tscn
into Unity), then has each editor report where every object ends up, including the objects inside placed prefabs,
and compares them (compare_probes.py).

    python tests/tools/check_prefabs_in_engines.py <Unity.exe> <Godot console exe> [work folder]
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from check_unity_addon import rmtree, twinscene  # noqa: E402
from compare_probes import compare  # noqa: E402

PROJECTS = ROOT / "tests" / "projects"
PAIRS = [
    {"unity": "Assets/Prefabs/Crate.prefab", "godot": "prefabs/crate.tscn"},
    {"unity": "Assets/Prefabs/Stack.prefab", "godot": "prefabs/stack.tscn"},
    {"unity": "Assets/Scenes/Prefabs.unity", "godot": "levels/level.tscn"},
    {"unity": "Assets/FromGodot/Crate.prefab", "godot": "scenes/crate.tscn"},
    {"unity": "Assets/FromGodot/Stack.prefab", "godot": "scenes/stack.tscn"},
    {"unity": "Assets/FromGodot/Level.unity", "godot": "scenes/prefabs.tscn"},
]
CHECKS = [  # (Unity scene, Godot scene, what)
    ("Assets/Scenes/Prefabs.unity", "res://levels/level.tscn", "Unity level -> Godot"),
    ("Assets/FromGodot/Level.unity", "res://scenes/prefabs.tscn", "Godot level -> Unity"),
]


def main(unity: str, godot: str, ws: Path) -> int:
    rmtree(ws)
    ws.mkdir(parents=True)
    shutil.copytree(PROJECTS / "unity_ref", ws / "unity",
                    ignore=shutil.ignore_patterns("Library", "Temp", "Logs", "UserSettings"))
    shutil.copytree(PROJECTS / "godot_ref", ws / "godot", ignore=shutil.ignore_patterns(".godot"))
    manifest = ws / "unity" / "Packages" / "manifest.json"
    m = json.loads(manifest.read_text())
    m["dependencies"]["com.twinscene.unity"] = "file:" + (ROOT / "addons" / "unity").as_posix()
    manifest.write_text(json.dumps(m, indent=2))
    (ws / "twinscene.json").write_text(json.dumps({"shared": {"unity": "Assets/Shared", "godot": "shared"}, "scenes": PAIRS}))
    print(twinscene(ws, "sync"))

    probes = ws / "probes"
    probes.mkdir()
    env = dict(os.environ, TWINSCENE_PROBE_INTERNALS="1",
               TWINSCENE_PROBE_SCENE=";".join(u for u, _, _ in CHECKS),
               TWINSCENE_PROBE_OUT=";".join(str(probes / f"unity{i}.json") for i in range(len(CHECKS))))
    subprocess.run([unity, "-batchmode", "-nographics", "-projectPath", str(ws / "unity"),
                    "-executeMethod", "Probe.Run", "-quit", "-logFile", str(ws / "unity.log")], env=env)
    log = (ws / "unity.log").read_text(encoding="utf-8", errors="replace")
    for line in log.splitlines():
        if "error CS" in line or "Broken prefab" in line or "Missing Prefab" in line or "Problem detected" in line:
            print("UNITY", line)

    probe_gd = str(ROOT / "tests" / "tools" / "probe_godot.gd")
    subprocess.run([godot, "--headless", "--path", str(ws / "godot"), "--import"], capture_output=True, timeout=300)
    for i, (_, res, _) in enumerate(CHECKS):
        p = subprocess.run([godot, "--headless", "--path", str(ws / "godot"), "--script", probe_gd, "--", res,
                            str(probes / f"godot{i}.json")], capture_output=True, text=True, timeout=300)
        for line in (p.stdout + p.stderr).splitlines():
            if "ERROR" in line or "error" in line.lower() and "probe" not in line:
                print("GODOT", line)

    failed = 0
    for i, (_, _, what) in enumerate(CHECKS):
        uf, gf = probes / f"unity{i}.json", probes / f"godot{i}.json"
        if not uf.exists() or not gf.exists():
            print(f"{what}: a probe didn't run (unity {uf.exists()}, godot {gf.exists()})")
            failed += 1
            continue
        u, g = json.loads(uf.read_text()), json.loads(gf.read_text())
        problems = compare(u, g)
        print(f"{what}: {len(set(u) & set(g))} objects in both, {len(problems)} mismatches")
        for pr in problems:
            print("   ", pr)
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    work = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.gettempdir()) / "twinscene-prefabs"
    sys.exit(main(sys.argv[1], sys.argv[2], work))
