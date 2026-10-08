import math
import unittest
from pathlib import Path

from twinscene.godot import tscn, variant
from twinscene.godot.variant import Call, StringName

REF = Path(__file__).parent / "projects" / "godot_ref" / "scenes" / "reference.tscn"

TRICKY = '''[gd_scene load_steps=3 format=3 uid="uid://b8x2k"]

[ext_resource type="Script" uid="uid://c1" path="res://player.gd" id="1_abc"]

[sub_resource type="Animation" id="Animation_x"]
resource_name = "walk"
tracks/0/keys = {
"times": PackedFloat32Array(0, 0.5),
"values": [Vector3(0, 0, 0), Vector3(1, 0, -2.5)]
}

[node name="Player" type="CharacterBody3D"]
script = ExtResource("1_abc")
tags = Array[StringName]([&"hero", &"blue"])
note = "line one
line \\"two\\""
; a comment between properties
speed = 4.5

layers = 3
metadata/twinscene_id = 42

[connection signal="ready" from="." to="." method="_on_ready"]
'''


class RoundTrip(unittest.TestCase):
    def test_reference_scene_is_byte_identical(self):
        text = REF.read_text(encoding="utf-8")
        self.assertEqual(tscn.loads(text).dump(), text)

    def test_tricky_scene_is_byte_identical(self):
        self.assertEqual(tscn.loads(TRICKY).dump(), TRICKY)

    def test_parsed_values(self):
        doc = tscn.loads(TRICKY)
        self.assertEqual(doc.header.get("uid"), "uid://b8x2k")
        player = doc.nodes()[0]
        self.assertEqual(player.get("name"), "Player")
        self.assertEqual(player.value("script"), Call("ExtResource", ("1_abc",)))
        self.assertEqual(doc.resolve(player.value("script")).get("path"), "res://player.gd")
        self.assertEqual(player.value("note"), 'line one\nline "two"')
        self.assertEqual(player.value("tags"), Call("Array[StringName]", ([StringName("hero"), StringName("blue")],)))
        self.assertIsInstance(player.value("tags").args[0][0], StringName)
        self.assertEqual(player.value("speed"), 4.5)
        self.assertEqual(player.value("layers"), 3)
        anim = doc.sub_resource("Animation_x")
        self.assertEqual(anim.value("tracks/0/keys")["values"][1], Call("Vector3", (1, 0, -2.5)))
        conn = doc.by_tag("connection")[0]
        self.assertEqual(conn.get("method"), "_on_ready")


class Editing(unittest.TestCase):
    def setUp(self):
        self.text = REF.read_text(encoding="utf-8")
        self.doc = tscn.loads(self.text)

    def node(self, name):
        return next(s for s in self.doc.nodes() if s.get("name") == name)

    def changed_lines(self, new):
        a, b = self.text.splitlines(), new.splitlines()
        return [(x, y) for x, y in zip(a, b) if x != y], len(b) - len(a)

    def test_same_value_within_float_noise_changes_nothing(self):
        box = self.node("Box")
        t = box.value("transform")
        box.set("transform", Call("Transform3D", tuple(a + 1e-9 for a in t.args)))
        self.assertEqual(self.doc.dump(), self.text)

    def test_editing_one_value_touches_one_line(self):
        self.node("Lamp").set("omni_range", 12.5)
        diff, grown = self.changed_lines(self.doc.dump())
        self.assertEqual(grown, 0)
        self.assertEqual(diff, [("omni_range = 8.0", "omni_range = 12.5")])

    def test_new_property_goes_before_metadata(self):
        self.node("Box").set("visible", False)
        out = self.doc.dump()
        self.assertIn("mesh = SubResource(\"BoxMesh_t5ptn\")\nvisible = false\nmetadata/twinscene_id = 1001", out)

    def test_header_edit_and_new_node(self):
        self.node("Lamp").set_attr("name", "Lantern")
        sec = tscn.new_section("node", {"name": "Crate", "type": "Node3D", "parent": ".", "unique_id": 77},
                               {"transform": Call("Transform3D", (1, 0, 0, 0, 1, 0, 0, 0, 1, 0.5, 0, -1))})
        self.doc.insert(sec)
        out = self.doc.dump()
        self.assertIn('[node name="Lantern" type="OmniLight3D" parent="." unique_id=1769664112]', out)
        self.assertTrue(out.endswith(
            '\n\n[node name="Crate" type="Node3D" parent="." unique_id=77]\n'
            'transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0.5, 0, -1)\n'))
        again = tscn.loads(out)  # what we write, we can read
        self.assertEqual(again.dump(), out)

    def test_remove_last_section(self):
        self.doc.remove(self.node("Hidden Thing"))
        out = self.doc.dump()
        self.assertTrue(out.endswith("shape = SubResource(\"CapsuleShape3D_857mk\")\n"), out[-80:])


class Values(unittest.TestCase):
    def test_float_format_matches_godot(self):
        self.assertEqual(variant.dump(60.0), "60.0")
        self.assertEqual(variant.dump(0.8660254), "0.8660254")
        self.assertEqual(variant.dump(Call("Vector3", (1.0, -0.5, 0.0))), "Vector3(1, -0.5, 0)")
        self.assertEqual(variant.dump(Call("Color", (1.0, 0.95, 0.85, 1.0))), "Color(1, 0.95, 0.85, 1)")

    def test_special_values(self):
        self.assertTrue(math.isinf(variant.parse_all("inf")))
        self.assertEqual(variant.parse_all("Vector2(inf_neg, 0)").args[0], -math.inf)
        self.assertEqual(variant.parse_all('^"Path/To"'), Call("NodePath", ("Path/To",)))
        self.assertEqual(variant.parse_all("{}"), {})
        self.assertEqual(variant.parse_all("[1, 2,]"), [1, 2])

    def test_dump_parse_round_trip(self):
        for v in [None, True, -3, 2.5, "a\"b\\c\nd", StringName("x"), [1, [2.5, "z"]], {"k": Call("Vector3", (1, 2, 3))},
                  Call("PackedStringArray", ("a", "b")), Call("Array[int]", ([1, 2],))]:
            self.assertTrue(variant.same(variant.parse_all(variant.dump(v)), v), v)


if __name__ == "__main__":
    unittest.main()
