import unittest
from pathlib import Path

from twinscene.unity import yaml
from twinscene.unity.yaml import FlowMap

PROJ = Path(__file__).parent / "projects" / "unity_ref" / "Assets"
REF = PROJ / "Scenes" / "Reference.unity"

TRICKY = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100
GameObject:
  m_Name: 'Door: left #2'
  m_TagString: "tab\\there"
  m_Component:
  - component: {fileID: 101}
  m_Empty:
  m_Long: this is a long plain value that Unity
    wrapped onto a second line
--- !u!1001 &200
PrefabInstance:
  m_Modification:
    serializedVersion: 3
    m_TransformParent: {fileID: 0}
    m_Modifications:
    - target: {fileID: 400000, guid: 0123456789abcdef0123456789abcdef, type: 3}
      propertyPath: m_LocalPosition.x
      value: 1.5
      objectReference: {fileID: 0}
    - target: {fileID: 100000, guid: 0123456789abcdef0123456789abcdef, type: 3}
      propertyPath: m_Name
      value: Crate
      objectReference: {fileID: 0}
    m_RemovedComponents: []
  m_SourcePrefab: {fileID: 100100000, guid: 0123456789abcdef0123456789abcdef, type: 3}
--- !u!4 &300 stripped
Transform:
  m_CorrespondingSourceObject: {fileID: 400000, guid: 0123456789abcdef0123456789abcdef, type: 3}
  m_PrefabInstance: {fileID: 200}
"""


class RoundTrip(unittest.TestCase):
    def test_unity_saved_files_are_byte_identical(self):
        files = [REF] + sorted(PROJ.glob("**/*.mat")) + sorted(PROJ.glob("**/*.meta"))
        self.assertGreater(len(files), 3)
        for f in files:
            if f.suffix == ".meta":
                continue  # .meta files are plain YAML without '--- !u!' objects; covered elsewhere
            text = f.read_text(encoding="utf-8")
            with self.subTest(f.name):
                self.assertEqual(yaml.loads(text).dump(), text)

    def test_tricky_is_byte_identical(self):
        self.assertEqual(yaml.loads(TRICKY).dump(), TRICKY)

    def test_values(self):
        doc = yaml.loads(TRICKY)
        go = doc.by_id(100)
        self.assertEqual(go.type_name, "GameObject")
        self.assertEqual(go.get("m_Name"), "Door: left #2")
        self.assertEqual(go.get("m_TagString"), "tab\there")
        self.assertEqual(yaml.ref(go.get("m_Component", 0, "component")), 101)
        self.assertEqual(go.get("m_Empty"), "")
        self.assertEqual(go.get("m_Long"), "this is a long plain value that Unity wrapped onto a second line")
        pi = doc.by_id(200)
        mods = pi.get("m_Modification", "m_Modifications")
        self.assertEqual(len(mods), 2)
        self.assertEqual(mods[1]["value"], "Crate")
        self.assertEqual(mods[0]["target"]["guid"], "0123456789abcdef0123456789abcdef")
        self.assertTrue(doc.by_id(300).stripped)

    def test_reference_scene_values(self):
        doc = yaml.load(REF)
        names = {o.get("m_Name") for o in doc.of_class(1)}
        self.assertLessEqual({"Box", "Child", "Main Camera", "Sun", "Lamp", "Spot", "Floor", "Ball", "Pill"}, names)
        cam = doc.of_class(20)[0]
        self.assertEqual(cam.get("field of view"), "60")
        self.assertEqual(cam.get("near clip plane"), "0.3")


class Editing(unittest.TestCase):
    def setUp(self):
        self.text = REF.read_text(encoding="utf-8")
        self.doc = yaml.loads(self.text)

    def go(self, name):
        return next(o for o in self.doc.of_class(1) if o.get("m_Name") == name)

    def transform_of(self, name):
        go = self.go(name)
        return next(o for o in self.doc.of_class(4) if yaml.ref(o.get("m_GameObject")) == go.file_id)

    def diff(self, new):
        a, b = self.text.splitlines(), new.splitlines()
        return [(x, y) for x, y in zip(a, b) if x != y], len(b) - len(a)

    def test_unchanged_value_keeps_text(self):
        t = self.transform_of("Lamp")
        t.set("m_LocalPosition", FlowMap(x=-2.0000001, y=3, z=0))
        self.assertEqual(self.doc.dump(), self.text)

    def test_one_edit_one_line(self):
        self.transform_of("Lamp").set("m_LocalPosition", FlowMap(x=-2.5, y=3, z=0.125))
        self.go("Lamp").set("m_Name", "Lantern: old")
        diff, grown = self.diff(self.doc.dump())
        self.assertEqual(grown, 0)
        self.assertEqual(diff, [
            ("  m_Name: Lamp", "  m_Name: 'Lantern: old'"),
            ("  m_LocalPosition: {x: -2, y: 3, z: 0}", "  m_LocalPosition: {x: -2.5, y: 3, z: 0.125}"),
        ])
        again = yaml.loads(self.doc.dump())
        self.assertEqual(again.dump(), self.doc.dump())

    def test_list_edit_and_new_key(self):
        go = self.go("Lamp")
        comps = list(go.get("m_Component"))
        comps.append({"component": FlowMap(fileID=999)})
        go.set("m_Component", comps)
        go.set(("m_NewThing",), FlowMap(fileID=0))
        out = self.doc.dump()
        self.assertIn("  - component: {fileID: 447962130}\n  - component: {fileID: 999}\n", out)
        self.assertIn("  m_IsActive: 1\n  m_NewThing: {fileID: 0}\n--- ", out)

    def test_nested_edit(self):
        light = next(o for o in self.doc.of_class(108) if yaml.ref(o.get("m_GameObject")) == self.go("Sun").file_id)
        light.set(("m_Shadows", "m_Type"), 0)
        diff, grown = self.diff(self.doc.dump())
        self.assertEqual(diff, [("    m_Type: 2", "    m_Type: 0")])

    def test_add_object(self):
        self.doc.add(1, 12345, "GameObject", {
            "m_ObjectHideFlags": 0, "serializedVersion": 6,
            "m_Component": [{"component": FlowMap(fileID=12346)}],
            "m_Name": "New One", "m_IsActive": 1,
        })
        out = self.doc.dump()
        # Unity keeps objects in file id order: 12345 goes after &4 (NavMeshSettings), before the first GameObject
        self.assertIn(
            "--- !u!1 &12345\nGameObject:\n  m_ObjectHideFlags: 0\n  serializedVersion: 6\n  m_Component:\n"
            "  - component: {fileID: 12346}\n  m_Name: New One\n  m_IsActive: 1\n--- !u!1 &447962129\n", out)
        self.assertEqual(yaml.loads(out).by_id(12345).get("m_Name"), "New One")


class Floats(unittest.TestCase):
    def test_like_unity(self):
        self.assertEqual(yaml.fmt_float(1.0), "1")
        self.assertEqual(yaml.fmt_float(0.5), "0.5")
        self.assertEqual(yaml.fmt_float(0.40821794), "0.40821794")
        self.assertEqual(yaml.fmt_float(0.70710677), "0.70710677")
        self.assertEqual(yaml.fmt_float(-8.742278e-08), "-8.742278e-8")


if __name__ == "__main__":
    unittest.main()
