"""Both readers on scenes saved by the real editors (tests/tools/make_godot_ref.gd, Assets/Editor/MakeRef.cs)."""

import unittest
from pathlib import Path

from twinscene import coords
from twinscene.godot import reader as godot_reader, tscn
from twinscene.model import same, same_rotation
from twinscene.project import GodotProject, UnityProject
from twinscene.unity import reader as unity_reader, yaml

HERE = Path(__file__).parent / "projects"


def by_path(scene):
    return {scene.path(k): n for k, n in scene.nodes.items()}


class UnityReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proj = UnityProject(HERE / "unity_ref", "Assets/Shared")
        cls.scene = unity_reader.read(yaml.load(HERE / "unity_ref/Assets/Scenes/Reference.unity"), proj)
        cls.n = by_path(cls.scene)

    def test_hierarchy_and_order(self):
        roots = [n.name for n in self.scene.roots()]
        self.assertEqual(roots, ["Box", "Main Camera", "Sun", "Lamp", "Spot", "Floor", "Ball", "Hidden Thing", "Axis Marker"])
        self.assertIn("Box/Child", self.n)
        self.assertIn("Ball/Pill", self.n)
        self.assertEqual(self.scene.warnings, [])

    def test_transforms_are_mirrored(self):
        box = self.n["Box"]
        self.assertTrue(same(box.get("position"), (-1, 2, 3)))
        self.assertTrue(same_rotation(box.get("rotation"), coords.euler_deg(0, -30, 0)))
        child = self.n["Box/Child"]
        self.assertTrue(same(child.get("scale"), (1, 2, 3)))
        # Unity euler (10, 20, 30) mirrored: x stays, y and z flip
        self.assertTrue(same_rotation(child.get("rotation"), coords.mirror_rot(coords.euler_deg(10, 20, 30))))

    def test_camera_and_lights(self):
        cam = self.n["Main Camera"]
        self.assertTrue(same(coords.qrot(cam.get("rotation"), (0, 0, -1)), (0, 0, 1)))  # looks toward the scene
        self.assertEqual((cam.get("camera.fov"), cam.get("camera.near"), cam.get("camera.far")), (60, 0.3, 1000))
        sun = self.n["Sun"]
        self.assertEqual(sun.get("light.type"), "directional")
        self.assertTrue(sun.get("light.shadows"))
        self.assertTrue(same(sun.get("light.color"), (1, 0.95, 0.85)))
        # Unity's sun shines along its +Z; the same direction mirrored is the Godot sun's -Z
        unity_dir = coords.qrot(coords.euler_deg(50, -30, 0), (0, 0, 1))
        self.assertTrue(same(coords.qrot(sun.get("rotation"), (0, 0, -1)), coords.mirror_pos(unity_dir)))
        self.assertEqual(self.n["Spot"].get("light.spot_angle"), 60)
        self.assertEqual(self.n["Lamp"].get("light.range"), 8)

    def test_mesh_material_physics(self):
        box = self.n["Box"]
        self.assertEqual(box.get("mesh"), ("primitive", "box"))
        mat = self.scene.materials[box.get("materials")[0]]
        self.assertTrue(same(mat.fields["albedo"], (0.8, 0.2, 0.1, 1)))
        self.assertTrue(same(mat.fields["metallic"], 0.3) and same(mat.fields["roughness"], 0.6))
        self.assertEqual(self.n["Floor/Shape"].get("collider.size"), (20, 1, 20))
        self.assertEqual(self.n["Ball"].get("body.mass"), 2.5)
        self.assertEqual(self.n["Ball"].get("collider.radius"), 0.75)
        self.assertEqual(self.n["Ball/Pill"].get("collider.height"), 1.8)
        self.assertFalse(self.n["Hidden Thing"].get("active"))
        self.assertEqual(self.n["Axis Marker"].get("mesh"), ("asset", "shared:axis_marker.obj"))


class GodotReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        doc = tscn.load(HERE / "godot_ref/scenes/reference.tscn")
        cls.scene = godot_reader.read(doc, GodotProject(HERE / "godot_ref"), "res://scenes/reference.tscn")
        cls.n = by_path(cls.scene)

    def test_keys_are_godot_unique_ids(self):
        self.assertEqual(self.n["Box"].key, "g:629817099")

    def test_values(self):
        box = self.n["Box"]
        self.assertTrue(same(box.get("position"), (1, 2, 3)))
        self.assertTrue(same_rotation(box.get("rotation"), coords.euler_deg(0, 30, 0)))
        self.assertEqual(box.get("mesh.size"), (2, 1, 0.5))
        child = self.n["Box/Child"]
        self.assertTrue(same(child.get("scale"), (1, 2, 3)))
        self.assertTrue(same_rotation(child.get("rotation"), coords.euler_deg(10, 20, 30)))
        self.assertEqual(self.n["Spot"].get("light.spot_angle"), 60)  # Godot 30 is half of a 60 cone
        self.assertEqual(self.n["Lamp"].get("light.shadows"), False)  # default, not in the file
        self.assertEqual(self.n["Floor"].get("body.type"), "static")
        self.assertEqual(self.n["Ball/Pill"].get("collider.shape"), "capsule")
        self.assertFalse(self.n["Hidden Thing"].get("active"))


if __name__ == "__main__":
    unittest.main()
