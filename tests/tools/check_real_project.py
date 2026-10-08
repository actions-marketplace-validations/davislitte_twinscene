"""Checks a synced TwinScene repository inside both real editors: every scene pair is probed in Unity and in Godot
(objects inside placed prefabs included) and compared with compare_probes.py.

    python tests/tools/check_real_project.py <repo with twinscene.json> <Unity.exe> <Godot console exe>

Unity opens a copy of the Unity project without its C# scripts (so projects whose scripts need packages that
aren't installed still open; scenes just show missing scripts) and with the probe script added.
"""

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from check_unity_addon import rmtree  # noqa: E402
from compare_probes import compare  # noqa: E402
from twinscene.project import Config  # noqa: E402
from twinscene.sync import scene_pairs  # noqa: E402


def main(repo: Path, unity: str, godot: str) -> int:
    cfg = Config.load(repo)
    pairs = [p for p in scene_pairs(cfg) if p.kind == "scene"]
    work = repo / ".probe"  # kept between runs: Unity's import cache in it makes the next run much faster
    work.mkdir(exist_ok=True)
    for old in work.glob("*.json"):
        old.unlink()

    # Unity: a script-free copy with the probe (refreshed in place, leaving Unity's Library alone)
    uproj = work / "unity"
    if (uproj / "Assets").exists():
        rmtree(uproj / "Assets")
    shutil.copytree(cfg.unity, uproj, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("Library", "Temp", "Logs", "UserSettings", "*.cs", "*.cs.meta",
                                                  "*.asmdef", "*.asmdef.meta"))
    (uproj / "Assets" / "Editor").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "tests/projects/unity_ref/Assets/Editor/Probe.cs", uproj / "Assets/Editor/TwinSceneProbe.cs")
    manifest = uproj / "Packages" / "manifest.json"
    if manifest.exists():
        m = json.loads(manifest.read_text())
        m["dependencies"] = {k: v for k, v in m["dependencies"].items() if not str(v).startswith("file:")}
        m["dependencies"]["com.twinscene.unity"] = "file:" + (ROOT / "addons" / "unity").as_posix()  # helper marker
        manifest.write_text(json.dumps(m, indent=2))
    (uproj / "Packages" / "packages-lock.json").unlink(missing_ok=True)
    env = dict(os.environ, TWINSCENE_PROBE_INTERNALS="1",
               TWINSCENE_PROBE_SCENE=";".join(p.unity for p in pairs),
               TWINSCENE_PROBE_OUT=";".join(str(work / f"u{i}.json") for i in range(len(pairs))))
    subprocess.run([unity, "-batchmode", "-nographics", "-projectPath", str(uproj), "-executeMethod", "Probe.Run",
                    "-quit", "-logFile", str(work / "unity.log")], env=env, timeout=3600)
    ulog = (work / "unity.log").read_text(encoding="utf-8", errors="replace")
    for line in sorted(set(l for l in ulog.splitlines() if "error CS" in l))[:10]:
        print("UNITY", line)

    # Godot
    gp = str(cfg.godot)
    subprocess.run([godot, "--headless", "--path", gp, "--import"], capture_output=True, timeout=1800)
    probe = str(ROOT / "tests/tools/probe_godot.gd")
    gerrors = Counter()
    for i, p in enumerate(pairs):
        r = subprocess.run([godot, "--headless", "--path", gp, "--script", probe, "--", p.godot, str(work / f"g{i}.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        for line in (r.stdout + r.stderr).splitlines():
            if line.startswith(("ERROR", "SCRIPT ERROR")):
                gerrors[line[:160]] += 1
    for line, n in gerrors.most_common(10):
        print(f"GODOT ({n}x) {line}")

    total = bad = 0
    kinds = Counter()
    for i, p in enumerate(pairs):
        uf, gf = work / f"u{i}.json", work / f"g{i}.json"
        if not uf.exists() or not gf.exists():
            print(f"{p.name}: probe missing (unity {uf.exists()}, godot {gf.exists()})")
            bad += 1
            continue
        u, g = json.loads(uf.read_text()), json.loads(gf.read_text())
        problems = compare(u, g)
        both = len(set(u) & set(g))
        total += both
        bad += len(problems)
        print(f"{p.name}: {both} objects in both, {len(u)} in Unity, {len(g)} in Godot, {len(problems)} mismatches")
        for pr in problems:
            kinds[pr.split(": ", 1)[1].split(" Unity")[0] if " Unity" in pr else pr.split(": ", 1)[1]] += 1
        for pr in problems[:6]:
            print("   ", pr)
    print(f"\nTOTAL: {total} objects compared, {bad} mismatches")
    for k, n in kinds.most_common(12):
        print(f"  {n:4} x {k}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
