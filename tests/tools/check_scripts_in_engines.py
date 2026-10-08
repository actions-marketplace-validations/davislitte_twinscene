"""Script pairing, checked inside both real editors.

A Unity C# script (Spinner.cs) and a GDScript (spinner.gd) are paired. TwinScene adds the Unity script to an object
and syncs it to Godot; a Godot node with the GDScript is synced to Unity. Then each editor opens the scene, reports
every Spinner and its settings, and saves the scene in its own format; a last sync must find nothing to do.

    python tests/tools/check_scripts_in_engines.py <Unity.exe> <Godot console exe> [work folder]
"""

import json
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

from check_unity_addon import rmtree, twinscene  # noqa: E402
from twinscene.godot import tscn  # noqa: E402
from twinscene.godot.variant import Call  # noqa: E402
from twinscene.project import Config, UnityProject  # noqa: E402
from twinscene.scripts import ScriptTable  # noqa: E402
from twinscene.unity import reader as ur, writer as uw, yaml  # noqa: E402
from tests.test_scripts import GODOT_SPINNER, GUID, UNITY_SPINNER  # noqa: E402

PROJECTS = ROOT / "tests" / "projects"
UNITY_SCENE, GODOT_SCENE = "Assets/Scenes/Reference.unity", "levels/main.tscn"

UNITY_CHECK = r"""using UnityEditor; using UnityEditor.SceneManagement; using UnityEngine;
public static class ScriptCheck { public static void Run() {
    var scene = EditorSceneManager.OpenScene("Assets/Scenes/Reference.unity");
    foreach (var root in scene.GetRootGameObjects()) {
        foreach (var mb in root.GetComponentsInChildren<MonoBehaviour>(true)) {
            if (mb == null || mb.GetType().Name != "Spinner") continue;
            var so = new SerializedObject(mb);
            var a = so.FindProperty("axis").vector3Value; var c = so.FindProperty("tint").colorValue;
            Debug.Log("SPIN " + mb.gameObject.name + " speed=" + so.FindProperty("speed").floatValue.ToString("0.###")
                + " axis=" + a.x.ToString("0.###") + "," + a.y.ToString("0.###") + "," + a.z.ToString("0.###")
                + " label=" + so.FindProperty("label").stringValue + " clockwise=" + so.FindProperty("clockwise").boolValue
                + " tint=" + c.r.ToString("0.###") + "," + c.g.ToString("0.###") + "," + c.b.ToString("0.###"));
        }
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
            if (GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(t.gameObject) > 0) Debug.Log("MISSING " + t.name);
    }
    EditorSceneManager.MarkSceneDirty(scene);
    EditorSceneManager.SaveScene(scene);
}}
"""

GODOT_CHECK = """extends SceneTree
func _init():
	var ps: PackedScene = load("res://levels/main.tscn")
	var root = ps.instantiate()
	for n in root.find_children("*", "", true, false):
		var s = n.get_script()
		if s == null or not s.resource_path.ends_with("spinner.gd"):
			continue
		var a: Vector3 = n.axis
		var c: Color = n.tint
		print("SPIN %s speed=%s axis=%s,%s,%s label=%s clockwise=%s tint=%s,%s,%s" % [
			n.name if not n.has_meta("twinscene_part") else n.get_parent().name, snapped(n.speed, 0.001),
			snapped(a.x, 0.001), snapped(a.y, 0.001), snapped(a.z, 0.001), n.label,
			"True" if n.clockwise else "False", snapped(c.r, 0.001), snapped(c.g, 0.001), snapped(c.b, 0.001)])
	var out := PackedScene.new()
	out.pack(root)
	ResourceSaver.save(out, "res://levels/main.tscn")
	quit()
"""

# what each editor must report: name -> (speed, axis, label, clockwise, tint) as Unity sees them (axis in Unity's
# space: X mirrored from Godot's)
EXPECT_UNITY = {"Box": "speed=45 axis=1,0,0 label=box spinner clockwise=False tint=1,1,1",
                "Turner": "speed=30 axis=0,1,0 label=spin clockwise=True tint=1,0,0"}
EXPECT_GODOT = {"Box": "speed=45 axis=-1,0,0 label=box spinner clockwise=False tint=1,1,1",
                "Turner": "speed=30 axis=0,1,0 label=spin clockwise=True tint=1,0,0"}


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
    (ws / "unity" / "Assets" / "Scripts").mkdir()
    (ws / "unity" / "Assets" / "Scripts" / "Spinner.cs").write_text(UNITY_SPINNER)
    (ws / "unity" / "Assets" / "Scripts" / "Spinner.cs.meta").write_text(
        f"fileFormatVersion: 2\nguid: {GUID}\nMonoImporter:\n  externalObjects: {{}}\n  serializedVersion: 2\n"
        "  defaultReferences: []\n  executionOrder: 0\n  icon: {instanceID: 0}\n  userData: \n"
        "  assetBundleName: \n  assetBundleVariant: \n")
    (ws / "unity" / "Assets" / "Editor" / "ScriptCheck.cs").write_text(UNITY_CHECK)
    (ws / "godot" / "scripts").mkdir()
    (ws / "godot" / "scripts" / "spinner.gd").write_text(GODOT_SPINNER)
    (ws / "twinscene.json").write_text(json.dumps({"shared": {"unity": "Assets/Shared", "godot": "shared"},
                                                   "scenes": [{"unity": UNITY_SCENE, "godot": GODOT_SCENE}]}))
    print(twinscene(ws, "sync"))

    # Unity side: Box gets a Spinner (as if added in Unity's inspector)
    cfg = Config.load(ws)
    upath = cfg.unity / UNITY_SCENE
    doc = yaml.load(upath)
    proj = UnityProject(cfg.unity, cfg.shared_unity)
    proj.scripts = ScriptTable.load(cfg)
    ed = uw.UnityEditor(doc, proj, random.Random(5))
    box = next(k for k, n in ur.read(doc, proj).nodes.items() if n.name == "Box")
    c = ed._add_script(ed.obj(box), GUID)
    for k, v in (("speed", 45.0), ("axis", yaml.FlowMap(x=1.0, y=0.0, z=0.0)), ("label", "box spinner")):
        c.set(k, v)
    yaml.save(doc, upath)
    # Godot side: a new node with the GDScript and a few settings
    gpath = cfg.godot / GODOT_SCENE
    gdoc = tscn.load(gpath)
    gdoc.insert(tscn.new_section("ext_resource", {"type": "Script", "path": "res://scripts/spinner.gd", "id": "9_spin"}),
                after=gdoc.by_tag("ext_resource")[-1] if gdoc.by_tag("ext_resource") else gdoc.header)
    gdoc.insert(tscn.new_section("node", {"name": "Turner", "type": "Node3D", "parent": ".", "unique_id": 4242},
                                 {"script": Call("ExtResource", ("9_spin",)), "speed": 30.0, "clockwise": True,
                                  "tint": Call("Color", (1.0, 0.0, 0.0, 1.0))}), after=gdoc.nodes()[0])
    tscn.save(gdoc, gpath)
    print(twinscene(ws, "sync"))

    failed = 0
    subprocess.run([unity, "-batchmode", "-nographics", "-projectPath", str(ws / "unity"),
                    "-executeMethod", "ScriptCheck.Run", "-quit", "-logFile", str(ws / "unity.log")])
    log = (ws / "unity.log").read_text(encoding="utf-8", errors="replace")
    seen = {}
    for line in log.splitlines():
        if "error CS" in line or line.startswith("MISSING") or "Problem detected" in line:
            print("UNITY", line)
            failed += 1
        m = re.match(r"SPIN (\S+) (.*)", line)
        if m:
            seen[m.group(1)] = m.group(2).strip()
    failed += report("Unity", seen, EXPECT_UNITY)

    (ws / "godot_check.gd").write_text(GODOT_CHECK)
    subprocess.run([godot, "--headless", "--path", str(ws / "godot"), "--import"], capture_output=True, timeout=300)
    p = subprocess.run([godot, "--headless", "--path", str(ws / "godot"), "--script", str(ws / "godot_check.gd")],
                       capture_output=True, text=True, timeout=300)
    seen = {}
    for line in (p.stdout + p.stderr).splitlines():
        if ("ERROR" in line or "SCRIPT ERROR" in line) and not re.search(r"leaked|still in use|Pages in use", line):
            print("GODOT", line)
            failed += 1
        m = re.match(r"SPIN (\S+) (.*)", line)
        if m:
            seen[m.group(1)] = m.group(2).strip()
    failed += report("Godot", seen, EXPECT_GODOT)

    # both editors saved the scene their own way: nothing to sync
    out = json.loads(twinscene(ws, "status", "--json"))
    pending = [c for s in out["scenes"] for c in s["to_unity"] + s["to_godot"] + [str(x) for x in s["conflicts"]]]
    print(f"after both editors re-saved: {len(pending)} changes pending")
    for c in pending:
        print("   ", c)
    failed += bool(pending)
    return 1 if failed else 0


def report(engine: str, seen: dict, expect: dict) -> int:
    bad = 0
    for name, want in expect.items():
        got = seen.get(name)
        ok = got is not None and _norm(got) == _norm(want)
        print(f"{engine}: {name}: {'ok' if ok else 'MISMATCH'}  {got}" + ("" if ok else f"  (expected {want})"))
        bad += not ok
    return bad


def _norm(s: str) -> str:
    s = re.sub(r"(\d)\.0(?=[,\s]|$)", lambda m: m.group(1), s)  # Godot prints 45.0, Unity 45
    return re.sub(r"-0(?=[,\s]|$)", "0", s)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    work = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.gettempdir()) / "twinscene-scripts"
    sys.exit(main(sys.argv[1], sys.argv[2], work))
