"""Two-way sync on copies of the reference projects (no engines needed; tests/tools/*probe* check in the engines)."""

import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from twinscene import coords, models, sync as S
from twinscene.godot import reader as godot_reader, tscn, writer as godot_writer
from twinscene.model import same, same_rotation
from twinscene.project import Config, GodotProject, UnityProject
from twinscene.unity import reader as unity_reader, writer as unity_writer, yaml

PROJECTS = Path(__file__).parent / "projects"
SHARED = {"unity": "Assets/Shared", "godot": "shared"}  # where the fixtures keep their shared model


class Workspace:
    def __init__(self, unity_scene="Assets/Scenes/Reference.unity", godot_scene="scenes/from_unity.tscn"):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(PROJECTS / "unity_ref" / "Assets", self.root / "unity" / "Assets",
                        ignore=shutil.ignore_patterns("FromGodot.unity*", "TwinScene*"))
        (self.root / "unity" / "Packages").mkdir()
        shutil.copy(PROJECTS / "unity_ref" / "Packages" / "manifest.json", self.root / "unity" / "Packages")
        shutil.copytree(PROJECTS / "godot_ref", self.root / "godot", ignore=shutil.ignore_patterns(".godot"))
        (self.root / "twinscene.json").write_text(json.dumps(
            {"shared": SHARED, "scenes": [{"name": "Main", "unity": unity_scene, "godot": godot_scene}]}))
        self.cfg = Config.load(self.root)
        self.pair = S.scene_pairs(self.cfg)[0]
        self.rng = random.Random(7)

    def sync(self, prefer=None):
        return S.sync_pair(self.cfg, self.pair, prefer=prefer, rng=self.rng)

    @property
    def upath(self):
        return self.cfg.unity / self.pair.unity

    @property
    def gpath(self):
        return self.cfg.godot / self.pair.godot[6:]

    def unity(self):
        return unity_reader.read(yaml.load(self.upath), UnityProject(self.cfg.unity, self.cfg.shared_unity))

    def godot(self):
        return godot_reader.read(tscn.load(self.gpath), GodotProject(self.cfg.godot, self.cfg.shared_godot),
                                 self.pair.godot)

    def edit_unity(self, fn):
        doc = yaml.load(self.upath)
        ed = unity_writer.UnityEditor(doc, UnityProject(self.cfg.unity, self.cfg.shared_unity), random.Random(1))
        scene = unity_reader.read(doc, ed.project)
        fn(ed, {scene.path(k): n for k, n in scene.nodes.items()})
        yaml.save(doc, self.upath)

    def edit_godot(self, fn):
        doc = tscn.load(self.gpath)
        ed = godot_writer.GodotEditor(doc, GodotProject(self.cfg.godot, self.cfg.shared_godot), self.pair.godot,
                                      random.Random(2))
        scene = godot_reader.read(doc, ed.project, self.pair.godot)
        fn(ed, {scene.path(k): n for k, n in scene.nodes.items()})
        ed.finish()
        tscn.save(doc, self.gpath)

    def close(self):
        self.tmp.cleanup()


def by_path(scene):
    return {scene.path(k): n for k, n in scene.nodes.items()}


def assert_twins(t: unittest.TestCase, U, G, fields=("position", "rotation", "scale", "active", "light.energy",
                                                     "light.range", "camera.fov", "body.mass", "collider.radius")):
    """Every Unity object has a Godot twin at the same path with the same values."""
    u, g = by_path(U), by_path(G)
    t.assertEqual(sorted(u), sorted(g))
    for p in u:
        for f in fields:
            a, b = u[p].get(f), g[p].get(f)
            if a is None:
                continue
            ok = same_rotation(a, b) if f == "rotation" else same(a, b)
            t.assertTrue(ok, f"{p} {f}: Unity {a} vs Godot {b}")


class UnityToGodot(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.first = self.ws.sync()

    def tearDown(self):
        self.ws.close()

    def test_first_sync_builds_godot_twin(self):
        self.assertEqual(len([x for x in self.first.to_godot if x.startswith("added")]), 12)
        assert_twins(self, self.ws.unity(), self.ws.godot())
        self.assertEqual(self.first.conflicts, [])

    def test_second_sync_changes_nothing(self):
        before_u, before_g = self.ws.upath.read_text(), self.ws.gpath.read_text()
        r = self.ws.sync()
        self.assertTrue(r.empty, r.markdown())
        self.assertEqual(self.ws.upath.read_text(), before_u)
        self.assertEqual(self.ws.gpath.read_text(), before_g)

    def test_unity_edits_reach_godot(self):
        def edit(ed, n):
            box = n["Box"]
            f = dict(box.fields, position=(-4.0, 2.0, 1.0))
            ed.apply(box.key, {"position": f["position"]}, f)
            lamp = n["Lamp"]
            f = dict(lamp.fields, name="Lantern", **{"light.energy": 3.0})
            ed.apply(lamp.key, {"name": "Lantern", "light.energy": 3.0}, f)
            ed.delete(n["Hidden Thing"].key)
            ed.create({"name": "Crate", "position": (1.0, 0.5, -2.0), "rotation": coords.IDENTITY,
                       "scale": (1.0, 1.0, 1.0), "mesh": ("primitive", "box"), "mesh.size": (1.0, 1.0, 1.0)},
                      n["Floor"].key)
        self.ws.edit_unity(edit)
        r = self.ws.sync()
        self.assertEqual(r.conflicts, [])
        self.assertTrue(r.to_godot and not [x for x in r.to_unity if not x.startswith("new material")], r.markdown())
        g = by_path(self.ws.godot())
        self.assertTrue(same(g["Box"].get("position"), (-4, 2, 1)))
        self.assertIn("Lantern", g)
        self.assertEqual(g["Lantern"].get("light.energy"), 3.0)
        self.assertNotIn("Hidden Thing", g)
        self.assertEqual(g["Floor/Crate"].get("mesh"), ("primitive", "box"))
        assert_twins(self, self.ws.unity(), self.ws.godot())
        self.assertTrue(self.ws.sync().empty)

    def test_godot_edits_reach_unity(self):
        def edit(ed, n):
            box = n["Box"]
            rot = coords.euler_deg(0, 75, 0)
            f = dict(box.fields, rotation=rot)
            ed.apply(box.key, {"rotation": rot}, f)
            ed.reparent(n["Box/Child"].key, None)  # move Child to the top level
            ball = n["Ball"]
            ed.apply(ball.key, {"body.mass": 9.0}, dict(ball.fields, **{"body.mass": 9.0}))
            ed.create({"name": "Torch", "position": (0.0, 2.0, 0.0), "rotation": coords.IDENTITY,
                       "scale": (1.0, 1.0, 1.0), "light.type": "point", "light.color": (1.0, 0.5, 0.2),
                       "light.energy": 2.0, "light.range": 6.0, "light.shadows": False}, None)
        self.ws.edit_godot(edit)
        r = self.ws.sync()
        self.assertEqual(r.conflicts, [])
        u = by_path(self.ws.unity())
        self.assertTrue(same_rotation(u["Box"].get("rotation"), coords.euler_deg(0, 75, 0)))
        self.assertIn("Child", u)
        self.assertEqual(u["Ball"].get("body.mass"), 9.0)
        self.assertEqual(u["Torch"].get("light.range"), 6.0)
        assert_twins(self, self.ws.unity(), self.ws.godot())
        self.assertTrue(self.ws.sync().empty)

    def test_unknown_settings_survive(self):
        # Unity: shadow strength isn't something TwinScene knows. Godot: a node's metadata.
        text = self.ws.upath.read_text()
        self.ws.upath.write_text(text.replace("m_Strength: 1", "m_Strength: 0.42", 1))
        self.ws.edit_godot(lambda ed, n: ed.sec(n["Ball"].key).set("metadata/note", "keep me"))
        self.ws.edit_godot(lambda ed, n: ed.sec(n["Ball"].key).set("linear_damp", 0.7))

        def move(ed, n):
            f = dict(n["Sun"].fields, position=(0.0, 9.0, 0.0))
            ed.apply(n["Sun"].key, {"position": f["position"]}, f)
        self.ws.edit_unity(move)
        self.ws.sync()
        self.assertIn("m_Strength: 0.42", self.ws.upath.read_text())
        g = self.ws.gpath.read_text()
        self.assertIn('metadata/note = "keep me"', g)
        self.assertIn("linear_damp = 0.7", g)

    def test_conflict_is_reported_and_kept(self):
        def unity_move(ed, n):
            f = dict(n["Box"].fields, position=(5.0, 0.0, 0.0))
            ed.apply(n["Box"].key, {"position": f["position"]}, f)

        def godot_move(ed, n):
            f = dict(n["Box"].fields, position=(0.0, 0.0, 5.0))
            ed.apply(n["Box"].key, {"position": f["position"]}, f)
        self.ws.edit_unity(unity_move)
        self.ws.edit_godot(godot_move)
        r = self.ws.sync()
        self.assertEqual([(c.path, c.field) for c in r.conflicts], [("Box", "position")])
        self.assertTrue(same(by_path(self.ws.unity())["Box"].get("position"), (5, 0, 0)))  # neither side touched
        self.assertTrue(same(by_path(self.ws.godot())["Box"].get("position"), (0, 0, 5)))
        self.assertEqual(len(self.ws.sync().conflicts), 1)  # still there next time
        r = self.ws.sync(prefer="godot")
        self.assertEqual(r.conflicts, [])
        self.assertTrue(same(by_path(self.ws.unity())["Box"].get("position"), (0, 0, 5)))
        self.assertTrue(self.ws.sync().empty)

    def test_same_change_on_both_sides_is_not_a_conflict(self):
        for edit in (self.ws.edit_unity, self.ws.edit_godot):
            edit(lambda ed, n: ed.apply(n["Ball"].key, {"body.mass": 4.0}, dict(n["Ball"].fields, **{"body.mass": 4.0})))
        r = self.ws.sync()
        self.assertEqual(r.conflicts, [])
        self.assertTrue(self.ws.sync().empty)

    def test_deleted_on_one_side_edited_on_the_other_is_kept(self):
        self.ws.edit_unity(lambda ed, n: ed.delete(n["Lamp"].key))
        self.ws.edit_godot(lambda ed, n: ed.apply(n["Lamp"].key, {"light.energy": 5.0},
                                                  dict(n["Lamp"].fields, **{"light.energy": 5.0})))
        self.ws.sync()
        self.assertEqual(by_path(self.ws.unity())["Lamp"].get("light.energy"), 5.0)
        self.assertIn("Lamp", by_path(self.ws.godot()))
        self.assertTrue(self.ws.sync().empty)


class GodotToUnity(unittest.TestCase):
    def test_first_sync_builds_unity_twin(self):
        ws = Workspace(unity_scene="Assets/Scenes/New.unity", godot_scene="scenes/reference.tscn")
        try:
            r = ws.sync()
            self.assertEqual(len([x for x in r.to_unity if x.startswith("added")]), 12)
            assert_twins(self, ws.unity(), ws.godot())
            u = by_path(ws.unity())
            self.assertEqual(u["Box"].get("mesh.size"), (2, 1, 0.5))  # via a scaled helper object
            self.assertTrue(ws.sync().empty)
        finally:
            ws.close()

    def test_both_sides_full_needs_a_preference(self):
        ws = Workspace(unity_scene="Assets/Scenes/Reference.unity", godot_scene="scenes/reference.tscn")
        try:
            with self.assertRaises(S.NeedsPreference):
                ws.sync()
            r = ws.sync(prefer="unity")
            self.assertEqual(r.conflicts, [])
            assert_twins(self, ws.unity(), ws.godot(), fields=("position", "rotation", "active"))
            self.assertTrue(ws.sync().empty)
        finally:
            ws.close()


if __name__ == "__main__":
    unittest.main()


class Colliders(unittest.TestCase):
    """Several colliders on one object, triggers, and models with physics (seen in real projects)."""

    def setUp(self):
        self.ws = Workspace()
        self.ws.sync()

    def tearDown(self):
        self.ws.close()

    def test_several_colliders_and_triggers_both_ways(self):
        def edit(ed, n):
            ball = n["Ball"]
            extra = {"collider2.shape": "box", "collider2.size": (0.5, 2.0, 0.5), "collider2.center": (0.0, 1.0, 0.0),
                     "collider2.trigger": False,
                     "collider3.shape": "sphere", "collider3.radius": 3.0, "collider3.center": (0.0, 0.0, 0.0),
                     "collider3.trigger": True}
            ed.apply(ball.key, extra, dict(ball.fields, **extra))
        self.ws.edit_unity(edit)
        self.ws.sync()
        g = by_path(self.ws.godot())["Ball"]
        self.assertEqual((g.get("collider2.shape"), g.get("collider2.size")), ("box", (0.5, 2.0, 0.5)))
        self.assertTrue(same(g.get("collider2.center"), (0, 1, 0)))
        self.assertEqual((g.get("collider3.radius"), g.get("collider3.trigger")), (3.0, True))
        text = self.ws.gpath.read_text()
        self.assertIn('type="Area3D" parent="Ball"', text)  # the trigger sits in an Area3D helper
        self.assertTrue(self.ws.sync().empty)

        # Godot side: the trigger becomes solid, the second collider goes away
        def gedit(ed, n):
            ball = n["Ball"]
            f = {k: v for k, v in ball.fields.items() if not k.startswith(("collider2.", "collider3."))}
            f.update({"collider2.shape": "sphere", "collider2.radius": 3.0, "collider2.center": (0.0, 0.0, 0.0),
                      "collider2.trigger": False})
            changed = {k: f.get(k) for k in set(ball.fields) | set(f) if k.startswith(("collider2.", "collider3."))}
            ed.apply(ball.key, changed, f)
        self.ws.edit_godot(gedit)
        self.assertNotIn("Area3D", self.ws.gpath.read_text())  # emptied helper removed
        self.ws.sync()
        u = by_path(self.ws.unity())["Ball"]
        self.assertEqual((u.get("collider2.shape"), u.get("collider2.trigger")), ("sphere", False))
        self.assertIsNone(u.get("collider3.shape"))
        self.assertEqual(self.ws.upath.read_text().count("SphereCollider:"), 2)
        self.assertTrue(self.ws.sync().empty)

    def test_model_with_physics_keeps_its_collider(self):
        # What a Unity object with an FBX/glTF mesh, a Rigidbody and a collider becomes in Godot.
        fields = {"name": "Prop", "position": (0.0, 1.0, 0.0), "rotation": coords.IDENTITY, "scale": (1.0, 1.0, 1.0),
                  "mesh": ("asset", "shared:prop.gltf"), "materials": ["unity:Assets/Materials/BoxRed.mat"],
                  "body.type": "rigid", "body.mass": 3.0, "collider.shape": "box", "collider.size": (1.0, 1.0, 1.0),
                  "collider.center": (0.0, 0.0, 0.0), "collider.trigger": False}
        warnings = []

        def edit(ed, n):
            ed.create(dict(fields, materials=[]), None)
            ed.create(fields, None)
            warnings.extend(ed.warnings)
        self.ws.edit_godot(edit)
        text = self.ws.gpath.read_text()
        self.assertIn('[node name="Prop" type="RigidBody3D"', text)
        self.assertIn('[node name="Prop (mesh)" parent="Prop"', text)
        self.assertIn('path="res://shared/prop.gltf"', text)
        g = by_path(self.ws.godot())["Prop"]
        self.assertEqual((g.get("mesh"), g.get("body.mass"), g.get("collider.shape")),
                         (("asset", "shared:prop.gltf"), 3.0, "box"))
        self.assertTrue(any("materials on a model mesh" in w for w in warnings))


class ModelMeshes(unittest.TestCase):
    """An object showing one mesh out of a multi-mesh model file (like ML-Agents' PushBlockCourt.fbx)."""

    def test_one_mesh_of_a_model_round_trips(self):
        ws = Workspace()
        try:
            shared = ws.cfg.unity / "Assets" / "Shared"
            (shared / "court.fbx").write_bytes(b"Kaydara FBX Binary  \x00\x1a\x00" + bytes(16))
            guid = "0123456789abcdef0123456789abcdef"
            (shared / "court.fbx.meta").write_text(
                "fileFormatVersion: 2\nguid: %s\nModelImporter:\n  serializedVersion: 22\n  fileIDToRecycleName:\n"
                "    100000: Ground\n    4300000: Walls\n    4300002: Ground\n  meshes:\n    globalScale: 1\n"
                "    useFileScale: 1\n" % guid)
            (ws.cfg.godot / "shared" / "court.fbx").write_bytes((shared / "court.fbx").read_bytes())
            text = ws.upath.read_text()
            mesh_line = "  m_Mesh: {fileID: 10202, guid: 0000000000000000e000000000000000, type: 0}"
            assert mesh_line in text
            ws.upath.write_text(text.replace(mesh_line, f"  m_Mesh: {{fileID: 4300002, guid: {guid}, type: 3}}"))
            ws.sync()
            g = by_path(ws.godot())["Box"]
            self.assertEqual((g.get("mesh"), g.get("mesh.name")), (("asset", "shared:court.fbx"), "Ground"))
            gtext = ws.gpath.read_text()
            self.assertIn('[ext_resource type="ArrayMesh" path="res://shared/court_Ground.res"', gtext)
            imp = (ws.cfg.godot / "shared" / "court.fbx.import").read_text()
            self.assertIn('"Ground": {', imp)
            self.assertIn("fbx/importer=0", imp)  # Godot's built-in FBX importer
            self.assertTrue(ws.sync().empty)  # (the snapshot already saw the extraction settings)
            # and back: a Godot edit of that object keeps Unity's mesh reference
            ws.edit_godot(lambda ed, n: ed.apply(n["Box"].key, {"position": (0.0, 9.0, 0.0)},
                                                  dict(n["Box"].fields, position=(0.0, 9.0, 0.0))))
            ws.sync()
            self.assertIn(f"m_Mesh: {{fileID: 4300002, guid: {guid}, type: 3}}", ws.upath.read_text())
        finally:
            ws.close()

    def test_mesh_with_unequal_pivots_is_offset_in_godot(self):
        """Unity centres such a mesh on its scaling pivot, Godot doesn't (ML-Agents' PushBlockCourt GoalArea)."""
        ws = Workspace()
        try:
            shared = ws.cfg.unity / "Assets" / "Shared"
            pivots = {"Ground": ((0.0, 0.0, -10.5), (0.0, 0.0, -1050.0)), "Walls": ((0, 5, 0), (0, 5, 0))}
            (shared / "court.fbx").write_bytes(_binary_fbx(pivots))
            self.assertEqual(models.fbx_scaling_pivots(shared / "court.fbx"), {"Ground": (0.0, 0.0, -1050.0)})
            guid = "0123456789abcdef0123456789abcdef"
            (shared / "court.fbx.meta").write_text(
                "fileFormatVersion: 2\nguid: %s\nModelImporter:\n  serializedVersion: 22\n  fileIDToRecycleName:\n"
                "    4300000: Walls\n    4300002: Ground\n  meshes:\n    globalScale: 1\n    useFileScale: 1\n" % guid)
            (ws.cfg.godot / "shared" / "court.fbx").write_bytes((shared / "court.fbx").read_bytes())
            text = ws.upath.read_text()
            mesh_line = "  m_Mesh: {fileID: 10202, guid: 0000000000000000e000000000000000, type: 0}"
            ws.upath.write_text(text.replace(mesh_line, f"  m_Mesh: {{fileID: 4300002, guid: {guid}, type: 3}}"))
            ws.sync()
            g = by_path(ws.godot())["Box"]
            self.assertEqual(g.get("mesh.import_scale"), 0.01)  # (FBX files are in centimetres unless they say)
            self.assertEqual(g.get("mesh.offset"), (0.0, 0.0, 10.5))
            self.assertIn("Transform3D(0.01, 0, 0, 0, 0.01, 0, 0, 0, 0.01, 0, 0, 10.5)", ws.gpath.read_text())
            doc = tscn.load(ws.gpath)
            box = next(s for s in doc.by_tag("node") if s.get("name") == "Box")
            self.assertIsNone(box.value("mesh"))  # the helper shows the mesh now, not the node itself as well
            self.assertTrue(ws.sync().empty)
        finally:
            ws.close()


def _binary_fbx(pivots: dict) -> bytes:
    """A minimal binary FBX 7.4 file: one mesh Model per name, with (RotationPivot, ScalingPivot) properties."""
    import struct

    def prop(v):
        if isinstance(v, bytes):
            return b"S" + struct.pack("<I", len(v)) + v
        return b"D" + struct.pack("<d", float(v))

    def node(at, name, props, kids):
        body = b"".join(prop(p) for p in props)
        head = 13 + len(name)
        inner, pos = b"", at + head + len(body)
        for k in kids:
            raw = node(pos, *k)
            inner += raw
            pos += len(raw)
        if kids:
            inner += bytes(13)
        end = at + head + len(body) + len(inner)
        return struct.pack("<III", end, len(props), len(body)) + bytes([len(name)]) + name + body + inner

    def p70(key, v):
        return (b"P", [key, b"Vector3D", b"Vector", b"", *v], [])

    model_list = [(b"Model", [i + 1, name.encode() + b"\x00\x01Model", b"Mesh"],
                   [(b"Properties70", [], [p70(b"RotationPivot", rp), p70(b"ScalingPivot", sp)])])
                  for i, (name, (rp, sp)) in enumerate(pivots.items())]
    head = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400)
    return head + node(len(head), b"Objects", [], model_list) + bytes(13)


class GodotShapesAndModels(unittest.TestCase):
    """Godot things Unity lacks: cylinder and endless-plane colliders, glTF models (seen in Godot's demos)."""

    def setUp(self):
        self.ws = Workspace(unity_scene="Assets/Scenes/New.unity", godot_scene="scenes/reference.tscn")
        self.ws.sync()

    def tearDown(self):
        self.ws.close()

    def test_cylinder_and_world_boundary(self):
        text = self.ws.gpath.read_text()
        text = text.replace('[sub_resource type="CapsuleShape3D" id="CapsuleShape3D_857mk"]\nradius = 0.4\nheight = 1.8',
                            '[sub_resource type="CylinderShape3D" id="CapsuleShape3D_857mk"]\nradius = 0.4\nheight = 1.8')
        text = text.replace('[sub_resource type="BoxShape3D" id="BoxShape3D_di658"]\nsize = Vector3(20, 1, 20)',
                            '[sub_resource type="WorldBoundaryShape3D" id="BoxShape3D_di658"]')
        self.ws.gpath.write_text(text)
        r = self.ws.sync()
        u = by_path(self.ws.unity())
        pill = u["Ball/Pill"]
        self.assertEqual((pill.get("collider.shape"), pill.get("collider.radius"), pill.get("collider.height")),
                         ("cylinder", 0.4, 1.8))
        self.assertIn("m_Mesh: {fileID: 10206, guid: 0000000000000000e000000000000000", self.ws.upath.read_text())
        floor = u["Floor/Shape"]
        self.assertEqual(floor.get("collider.size"), (10000, 1, 10000))
        self.assertTrue(any("10 km box" in w for w in r.warnings))
        self.assertTrue(self.ws.sync().empty)
        # a Unity edit elsewhere leaves Godot's real shapes alone
        self.ws.edit_unity(lambda ed, n: ed.apply(n["Ball"].key, {"body.mass": 7.0}, dict(n["Ball"].fields, **{"body.mass": 7.0})))
        self.ws.sync()
        g = self.ws.gpath.read_text()
        self.assertIn('type="CylinderShape3D"', g)
        self.assertIn('type="WorldBoundaryShape3D"', g)

    def test_gltf_model_goes_in_a_helper_with_a_placed_model(self):
        (self.ws.cfg.godot / "shared" / "hero.glb").write_bytes(b"glTF")
        ushared = self.ws.cfg.unity / "Assets" / "Shared"  # (what the shared-folder mirroring does)
        (ushared / "hero.glb").write_bytes(b"glTF")
        (ushared / "hero.glb.meta").write_text("fileFormatVersion: 2\nguid: 11112222333344445555666677778888\n")
        self.ws.edit_godot(lambda ed, n: ed.create(
            {"name": "Hero", "mesh": ("asset", "shared:hero.glb"), "position": (1.0, 0.0, 0.0),
             "rotation": coords.IDENTITY, "scale": (1.0, 1.0, 1.0), "active": True}, None))
        r = self.ws.sync()
        text = self.ws.upath.read_text()
        self.assertIn("m_SourcePrefab: {fileID: -4161369568681901532, guid:", text)  # glTFast's placeable prefab
        self.assertTrue(any("glTFast" in w for w in r.warnings))  # (this test project doesn't have it)
        u = by_path(self.ws.unity())
        self.assertEqual(u["Hero"].get("mesh"), ("asset", "shared:hero.glb"))
        self.assertTrue(same(u["Hero"].get("position"), (1, 0, 0)))
        self.assertTrue(self.ws.sync().empty)
