"""Script pairing: a Unity C# script and a Godot script stand for each other; their shared settings sync."""

import json
import unittest

from twinscene import scripts as S
from twinscene.godot import tscn
from twinscene.godot.variant import Call
from twinscene.project import Config
from twinscene.unity import yaml

from tests.test_sync import Workspace, by_path

UNITY_SPINNER = """using UnityEngine;

// Turns its object. { a brace in a comment }
public class Spinner : MonoBehaviour
{
    [Header("Turning")]
    public float speed = 90f;
    [SerializeField] private Vector3 axis = Vector3.up;
    public bool clockwise;
    public string label = "spin";
    public Color tint = Color.white;
    public int unityOnly = 3;
    public float[] steps;
    private float notSaved = 1f;

    void Update() { transform.Rotate(axis, speed * Time.deltaTime); }
}
"""
GODOT_SPINNER = """extends Node3D
## Turns its node.

@export var speed: float = 90.0
@export var axis := Vector3.UP
@export var clockwise := false
@export var label: String = "spin"
@export var tint: Color = Color.WHITE
@export var godot_only := 1

func _process(delta):
	rotate(axis, deg_to_rad(speed) * delta)
"""
GUID = "5f1c0a2b3c4d4e5f8a9b0c1d2e3f4a5b"


class Parsing(unittest.TestCase):
    def test_unity_fields(self):
        info = S.parse_csharp(UNITY_SPINNER, "Assets/Scripts/Spinner.cs", "unity")
        self.assertEqual((info.cls, info.base), ("Spinner", "MonoBehaviour"))
        got = {k: (s.name, s.type, s.default) for k, s in info.settings.items()}
        self.assertEqual(got["speed"], ("speed", "float", 90.0))
        self.assertEqual(got["axis"], ("axis", "vector3", (0.0, 1.0, 0.0)))
        self.assertEqual(got["clockwise"], ("clockwise", "bool", False))
        self.assertEqual(got["tint"], ("tint", "color", (1.0, 1.0, 1.0, 1.0)))
        self.assertEqual(got["steps"][1], "")  # an array: listed, not synced
        self.assertNotIn("not_saved", got)  # private without [SerializeField]

    def test_unity_vectors_are_mirrored_like_positions(self):
        info = S.parse_csharp("class A : MonoBehaviour { public Vector3 offset = new Vector3(1f, 2f, 3f); }",
                              "Assets/A.cs", "unity")
        self.assertEqual(info.settings["offset"].default, (-1.0, 2.0, 3.0))

    def test_gdscript_exports(self):
        info = S.parse_gdscript(GODOT_SPINNER + "\n@export_group(\"More\")\nvar not_exported := 2\n"
                                "@export_range(0, 10) var level: int = 4:\n\tset(v):\n\t\tlevel = v\n",
                                "res://scripts/spinner.gd")
        self.assertEqual(info.base, "Node3D")
        got = {k: (s.name, s.type, s.default) for k, s in info.settings.items()}
        self.assertEqual(got["axis"], ("axis", "vector3", (0.0, 1.0, 0.0)))
        self.assertEqual(got["level"], ("level", "int", 4))
        self.assertNotIn("not_exported", got)  # @export_group isn't an @export of the next line

    def test_godot_csharp_exports(self):
        info = S.parse_csharp("using Godot;\npublic partial class Spinner : CharacterBody3D {\n"
                              "  [Export] public float Speed { get; set; } = 90f;\n"
                              "  [Export] private Vector3 _axis = Vector3.Up;\n  public float NotExported = 1;\n}",
                              "res://Spinner.cs", "godot")
        self.assertEqual(info.base, "CharacterBody3D")
        self.assertEqual({k: s.name for k, s in info.settings.items()}, {"speed": "Speed", "axis": "_axis"})

    def test_names(self):
        for name in ("moveSpeed", "m_MoveSpeed", "MoveSpeed", "_move_speed", "move_speed"):
            self.assertEqual(S.snake(name), "move_speed")


class ScriptSync(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        (self.ws.cfg.unity / "Assets" / "Scripts").mkdir()
        (self.ws.cfg.unity / "Assets" / "Scripts" / "Spinner.cs").write_text(UNITY_SPINNER)
        (self.ws.cfg.unity / "Assets" / "Scripts" / "Spinner.cs.meta").write_text(
            f"fileFormatVersion: 2\nguid: {GUID}\nMonoImporter:\n  serializedVersion: 2\n")
        (self.ws.cfg.godot / "scripts").mkdir()
        (self.ws.cfg.godot / "scripts" / "spinner.gd").write_text(GODOT_SPINNER)

    def tearDown(self):
        self.ws.close()

    def table(self):
        return S.ScriptTable.load(self.ws.cfg)

    def test_pairing(self):
        t = self.table()
        pair = t.pairs["Spinner"]
        self.assertEqual((pair.godot.path, pair.guid), ("res://scripts/spinner.gd", GUID))
        self.assertEqual(sorted(pair.shared), ["axis", "clockwise", "label", "speed", "tint"])
        self.assertEqual(pair.only_unity, ["steps", "unityOnly"])
        self.assertEqual(pair.only_godot, ["godot_only"])

    def test_explicit_pair_in_twinscene_json(self):
        (self.ws.cfg.godot / "scripts" / "spinner.gd").rename(self.ws.cfg.godot / "scripts" / "turner.gd")
        self.assertEqual(self.table().pairs, {})
        data = json.loads((self.ws.root / "twinscene.json").read_text())
        data["scripts"] = {"Assets/Scripts/Spinner.cs": "scripts/turner.gd"}
        (self.ws.root / "twinscene.json").write_text(json.dumps(data))
        self.ws.cfg = Config.load(self.ws.root)
        self.assertEqual(self.table().pairs["Spinner"].godot.path, "res://scripts/turner.gd")

    def add_unity_spinner(self, path, speed):
        def fn(ed, n):
            ed.project.scripts = self.table()
            go = ed.obj(n[path].key)
            c = ed._add_script(go, GUID)
            c.set("speed", speed)
            c.set("axis", yaml.FlowMap(x=1.0, y=0.0, z=0.0))
        self.ws.edit_unity(fn)

    def test_unity_script_reaches_godot_and_back(self):
        self.ws.sync()
        self.add_unity_spinner("Box", 45.0)
        r = self.ws.sync()
        self.assertFalse(r.conflicts)
        text = self.ws.gpath.read_text()
        self.assertIn('[ext_resource type="Script" path="res://scripts/spinner.gd"', text)
        doc = tscn.load(self.ws.gpath)
        box = next(s for s in doc.nodes() if s.get("name") == "Box")
        self.assertIsNotNone(box.value("script"))
        self.assertEqual(box.value("speed"), 45.0)
        self.assertEqual(box.value("axis").args, (-1.0, 0.0, 0.0))  # mirrored like a position
        self.assertIsNone(box.value("label"))  # still the script's own default: left out, as Godot does
        self.assertTrue(self.ws.sync().empty)

        # a Godot edit of a setting comes back to Unity
        box.set("speed", 180.0)
        tscn.save(doc, self.ws.gpath)
        r = self.ws.sync()
        self.assertIn("Box: script.Spinner.speed", r.to_unity)
        u = yaml.load(self.ws.upath)
        mb = next(o for o in u.objects if o.class_id == 114 and (o.get("m_Script") or {}).get("guid") == GUID)
        self.assertEqual(float(mb.get("speed")), 180.0)
        self.assertTrue(self.ws.sync().empty)

    def test_godot_node_with_script_gets_it_in_unity(self):
        self.ws.sync()
        doc = tscn.load(self.ws.gpath)
        root = doc.nodes()[0]
        ext = tscn.new_section("ext_resource", {"type": "Script", "path": "res://scripts/spinner.gd", "id": "9_spin"})
        doc.insert(ext, after=doc.by_tag("ext_resource")[-1])
        node = tscn.new_section("node", {"name": "Turner", "type": "Node3D", "parent": ".", "unique_id": 4242},
                                {"script": Call("ExtResource", ("9_spin",)), "speed": 30.0,
                                 "clockwise": True})
        doc.insert(node, after=root)
        tscn.save(doc, self.ws.gpath)
        self.ws.sync()
        u = by_path(self.unity_with_scripts())["Turner"]
        self.assertEqual(u.get("scripts"), ("Spinner",))
        self.assertEqual((u.get("script.Spinner.speed"), u.get("script.Spinner.clockwise")), (30.0, True))
        self.assertTrue(self.ws.sync().empty)

    def unity_with_scripts(self):
        from twinscene.project import UnityProject
        from twinscene.unity import reader
        p = UnityProject(self.ws.cfg.unity, self.ws.cfg.shared_unity)
        p.scripts = self.table()
        return reader.read(yaml.load(self.ws.upath), p)

    def test_script_the_node_cant_hold_goes_on_a_helper_or_is_reported(self):
        (self.ws.cfg.godot / "scripts" / "spinner.gd").write_text(GODOT_SPINNER.replace("extends Node3D",
                                                                                          "extends Node"))
        self.ws.sync()
        self.add_unity_spinner("Box", 45.0)
        self.ws.sync()
        doc = tscn.load(self.ws.gpath)
        box = next(s for s in doc.nodes() if s.get("name") == "Box")
        # Box is a MeshInstance3D, which is a Node: the script fits on it
        self.assertIsNotNone(box.value("script"))

        (self.ws.cfg.godot / "scripts" / "spinner.gd").write_text(GODOT_SPINNER.replace("extends Node3D",
                                                                                          "extends CharacterBody3D"))
        self.add_unity_spinner("Ball", 10.0)
        r = self.ws.sync()
        self.assertTrue(any("extends CharacterBody3D" in w for w in r.warnings), r.warnings)

    def test_unpaired_scripts_are_reported_and_left_alone(self):
        (self.ws.cfg.godot / "scripts" / "spinner.gd").unlink()
        self.ws.sync()
        self.add_unity_spinner("Box", 45.0)
        r = self.ws.sync()
        self.assertTrue(any("no Godot partner" in w and "Spinner.cs (1)" in w for w in r.warnings), r.warnings)
        self.assertNotIn("Script", self.ws.gpath.read_text().split("[node", 1)[0])


class ScriptSettingsOnPlacedPrefabs(unittest.TestCase):
    """Five enemies from one prefab, each a different speed: settings changed on one placed copy."""

    def test_both_ways(self):
        from twinscene.project import UnityProject
        from twinscene.unity import reader as ur, writer as uw
        from tests.test_prefabs import UNITY_TO_GODOT, PrefabWorkspace
        import random
        ws = PrefabWorkspace(UNITY_TO_GODOT)
        try:
            (ws.cfg.unity / "Assets" / "Scripts").mkdir()
            (ws.cfg.unity / "Assets" / "Scripts" / "Spinner.cs").write_text(UNITY_SPINNER)
            (ws.cfg.unity / "Assets" / "Scripts" / "Spinner.cs.meta").write_text(f"fileFormatVersion: 2\nguid: {GUID}\n")
            (ws.cfg.godot / "scripts").mkdir()
            (ws.cfg.godot / "scripts" / "spinner.gd").write_text(GODOT_SPINNER)
            # the Crate prefab's root gets a Spinner (speed 60)...
            pf = ws.cfg.unity / "Assets/Prefabs/Crate.prefab"
            doc = yaml.load(pf)
            proj = UnityProject(ws.cfg.unity, ws.cfg.shared_unity)
            ed = uw.UnityEditor(doc, proj, random.Random(3), prefab=True)
            root = next(k for k, n in ur.read(doc, proj).nodes.items() if n.parent is None)
            mb = ed._add_script(ed.obj(root), GUID)
            mb.set("speed", 60.0)
            yaml.save(doc, pf)
            prefab_guid = proj.guid_of("Assets/Prefabs/Crate.prefab")
            # ... and the placed "Crate Big" spins faster, about another axis
            sc = ws.cfg.unity / "Assets/Scenes/Prefabs.unity"
            sdoc = yaml.load(sc)
            big = next(int(k[2:]) for k, n in ur.read(sdoc, proj).nodes.items() if n.name == "Crate Big")
            pi = sdoc.by_id(big)
            mods = list(pi.get("m_Modification", "m_Modifications"))
            for prop, val in (("speed", "200"), ("axis.x", "1"), ("axis.y", "0")):
                mods.append({"target": yaml.FlowMap(fileID=mb.file_id, guid=prefab_guid, type=3), "propertyPath": prop,
                             "value": val, "objectReference": yaml.FlowMap(fileID=0)})
            pi.set(("m_Modification", "m_Modifications"), mods)
            yaml.save(sdoc, sc)

            reports = ws.sync_all()
            self.assertFalse([w for r in reports for w in r.warnings if "speed" in w], reports)
            crate = tscn.load(ws.cfg.godot / "prefabs/crate.tscn").nodes()[0]
            self.assertEqual(crate.value("speed"), 60.0)
            level = tscn.load(ws.cfg.godot / "levels/level.tscn")
            big_g = next(s for s in level.nodes() if s.get("name") == "Crate Big")
            self.assertEqual(big_g.value("speed"), 200.0)
            self.assertEqual(big_g.value("axis").args, (-1.0, 0.0, 0.0))
            other = next(s for s in level.nodes() if s.get("name") == "Crate")
            self.assertIsNone(other.value("speed"))  # the other copy keeps the prefab's speed
            self.assertTrue(all(r.empty for r in ws.sync_all()))

            # Godot: the big crate spins at 300 now
            big_g.set("speed", 300.0)
            tscn.save(level, ws.cfg.godot / "levels/level.tscn")
            reports = ws.sync_all()
            self.assertEqual(sum(len(r.conflicts) for r in reports), 0)
            text = sc.read_text()
            self.assertIn("propertyPath: speed\n      value: 300", text)
            self.assertIn("propertyPath: axis.x\n      value: 1", text)
            self.assertTrue(all(r.empty for r in ws.sync_all()))
        finally:
            ws.close()


if __name__ == "__main__":
    unittest.main()
