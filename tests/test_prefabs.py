"""Prefabs: Unity .prefab <-> Godot scenes placed in other scenes, on the fixtures each editor saved
(Assets/Editor/MakePrefabs.cs, tests/tools/make_godot_prefabs.gd)."""

import json
import random
import unittest

from twinscene import coords, sync as S
from twinscene.godot import reader as gr, tscn, writer as gw
from twinscene.model import same, same_rotation
from twinscene.project import Config, GodotProject, UnityProject
from twinscene.unity import reader as ur, writer as uw, yaml

from .test_sync import SHARED, Workspace, by_path

UNITY_TO_GODOT = [
    {"unity": "Assets/Prefabs/Crate.prefab", "godot": "prefabs/crate.tscn"},
    {"unity": "Assets/Scenes/Prefabs.unity", "godot": "levels/level.tscn"},
    {"unity": "Assets/Prefabs/Stack.prefab", "godot": "prefabs/stack.tscn"},  # listed out of order on purpose
]
GODOT_TO_UNITY = [
    {"unity": "Assets/FromGodot/Level.unity", "godot": "scenes/prefabs.tscn"},
    {"unity": "Assets/FromGodot/Stack.prefab", "godot": "scenes/stack.tscn"},
    {"unity": "Assets/FromGodot/Crate.prefab", "godot": "scenes/crate.tscn"},
]


class PrefabWorkspace(Workspace):
    def __init__(self, scenes):
        super().__init__()
        (self.root / "twinscene.json").write_text(json.dumps({"shared": SHARED, "scenes": scenes}))
        self.cfg = Config.load(self.root)
        self.pairs = {p.godot: p for p in S.scene_pairs(self.cfg)}

    def sync_all(self, prefer=None):
        return S.sync(self.cfg, prefer=prefer, rng=self.rng)

    def g(self, res):
        pair = self.pairs[res]
        return gr.read(tscn.load(self.cfg.godot / res[6:]), GodotProject(self.cfg.godot, self.cfg.shared_godot), res,
                       prefab=pair.kind == "prefab")

    def u(self, res):
        pair = self.pairs[res]
        return ur.read(yaml.load(self.cfg.unity / pair.unity), UnityProject(self.cfg.unity, self.cfg.shared_unity))

    def gtext(self, res):
        return (self.cfg.godot / res[6:]).read_text(encoding="utf-8")


def assert_same_layout(t, U, G):
    u, g = by_path(U), by_path(G)
    t.assertEqual(sorted(u), sorted(g))
    for p in u:
        for f in ("position", "scale", "active"):
            t.assertTrue(same(u[p].get(f), g[p].get(f)), f"{p} {f}: {u[p].get(f)} vs {g[p].get(f)}")
        t.assertTrue(same_rotation(u[p].get("rotation"), g[p].get("rotation")), p)
        t.assertEqual(bool(u[p].get("prefab")), bool(g[p].get("prefab")), p)


class UnityPrefabsToGodot(unittest.TestCase):
    def setUp(self):
        self.ws = PrefabWorkspace(UNITY_TO_GODOT)
        self.reports = self.ws.sync_all()

    def tearDown(self):
        self.ws.close()

    def test_order_puts_prefabs_first_innermost_first(self):
        self.assertEqual([p.unity.rsplit("/", 1)[1] for p in S.scene_pairs(self.ws.cfg)],
                         ["Crate.prefab", "Stack.prefab", "Prefabs.unity"])

    def test_prefabs_become_godot_scenes(self):
        crate = by_path(self.ws.g("res://prefabs/crate.tscn"))
        self.assertEqual(sorted(crate), ["Crate", "Crate/Lid"])
        self.assertEqual(crate["Crate"].get("mesh"), ("primitive", "box"))
        self.assertEqual(crate["Crate/Lid"].get("light.type"), "point")
        text = self.ws.gtext("res://prefabs/crate.tscn")  # the crate's collider becomes Godot's static body + shape
        self.assertIn('[node name="Crate" type="MeshInstance3D"', text)
        self.assertIn('[node name="Crate (collider)" type="CollisionShape3D" parent="Crate (static)"', text)
        self.assertEqual(crate["Crate"].get("collider.size"), (1, 1, 1))
        stack = by_path(self.ws.g("res://prefabs/stack.tscn"))
        self.assertEqual(stack["Stack/Top"].get("prefab"), "godot:res://prefabs/crate.tscn")
        for res in ("res://prefabs/crate.tscn", "res://prefabs/stack.tscn", "res://levels/level.tscn"):
            assert_same_layout(self, self.ws.u(res), self.ws.g(res))

    def test_placed_prefabs_become_instances(self):
        level = by_path(self.ws.g("res://levels/level.tscn"))
        self.assertEqual(level["Crate Big"].get("prefab"), "godot:res://prefabs/crate.tscn")
        self.assertEqual(level["Stack"].get("prefab"), "godot:res://prefabs/stack.tscn")
        self.assertIn("Shelf/Crate On Shelf/Sticker", level)  # added to a placed prefab
        text = self.ws.gtext("res://levels/level.tscn")
        self.assertIn('instance=ExtResource(', text)
        self.assertNotIn("BoxMesh", text)  # the crate's mesh lives in crate.tscn, not in the level

    def test_resync_changes_nothing(self):
        r = self.ws.sync_all()
        self.assertTrue(all(x.empty for x in r), "\n".join(x.markdown() for x in r))

    def test_edit_inside_a_prefab_reaches_godot(self):
        upath = self.ws.cfg.unity / "Assets/Prefabs/Crate.prefab"
        doc = yaml.load(upath)
        ed = uw.UnityEditor(doc, UnityProject(self.ws.cfg.unity), random.Random(1), prefab=True)
        lid = by_path(ur.read(doc, ed.project))["Crate/Lid"]
        ed.apply(lid.key, {"light.range": 7.5}, dict(lid.fields, **{"light.range": 7.5}))
        yaml.save(doc, upath)
        self.ws.sync_all()
        self.assertEqual(by_path(self.ws.g("res://prefabs/crate.tscn"))["Crate/Lid"].get("light.range"), 7.5)

    def test_godot_instance_edits_reach_unity(self):
        res = "res://levels/level.tscn"
        f = self.ws.cfg.godot / res[6:]
        doc = tscn.load(f)
        ed = gw.GodotEditor(doc, GodotProject(self.ws.cfg.godot), res, random.Random(2))
        n = by_path(gr.read(doc, ed.project, res))
        moved = dict(n["Crate Big"].fields, position=(1.0, 1.0, 9.0), name="Crate Huge")
        ed.apply(n["Crate Big"].key, {"position": moved["position"], "name": "Crate Huge"}, moved)
        ed.create({"name": "Crate New", "prefab": "godot:res://prefabs/crate.tscn", "position": (0.0, 0.5, -6.0),
                   "rotation": coords.euler_deg(0, 90, 0), "scale": (1.0, 1.0, 1.0), "active": True}, None)
        ed.delete(n["Stack"].key)
        ed.finish()
        tscn.save(doc, f)
        r = self.ws.sync_all()
        self.assertEqual(sum(len(x.conflicts) for x in r), 0)
        u = by_path(self.ws.u(res))
        self.assertTrue(same(u["Crate Huge"].get("position"), (1, 1, 9)))
        self.assertEqual(u["Crate New"].get("prefab"), "unity:Assets/Prefabs/Crate.prefab")
        self.assertTrue(same_rotation(u["Crate New"].get("rotation"), coords.euler_deg(0, 90, 0)))
        self.assertNotIn("Stack", u)
        assert_same_layout(self, self.ws.u(res), self.ws.g(res))
        self.assertTrue(all(x.empty for x in self.ws.sync_all()))

    def test_godot_edits_inside_instances_survive(self):
        res = "res://levels/level.tscn"
        f = self.ws.cfg.godot / res[6:]
        text = f.read_text(encoding="utf-8") + (
            '\n[node name="Lid" parent="Crate Big"]\nomni_range = 9.0\n'
            '\n[node name="Tag" type="Node3D" parent="Crate Big/Lid"]\n')
        f.write_text(text, encoding="utf-8")
        self.ws.sync_all()  # take Godot's own state as the new baseline
        upath = self.ws.cfg.unity / "Assets/Scenes/Prefabs.unity"
        doc = yaml.load(upath)
        ed = uw.UnityEditor(doc, UnityProject(self.ws.cfg.unity), random.Random(3))
        big = by_path(ur.read(doc, ed.project))["Crate Big"]
        ed.apply(big.key, {"name": "Crate Renamed"}, dict(big.fields, name="Crate Renamed"))
        yaml.save(doc, upath)
        r = self.ws.sync_all()
        out = self.ws.gtext(res)
        self.assertIn('[node name="Crate Renamed"', out)
        self.assertIn('[node name="Lid" parent="Crate Renamed"]\nomni_range = 9.0', out)
        self.assertIn('[node name="Tag" type="Node3D" parent="Crate Renamed/Lid"]', out)
        self.assertTrue(any("inside this placed scene" in w for x in r for w in x.warnings))


class GodotScenesToUnity(unittest.TestCase):
    def setUp(self):
        self.ws = PrefabWorkspace(GODOT_TO_UNITY)
        self.ws.sync_all()

    def tearDown(self):
        self.ws.close()

    def test_scenes_become_prefabs(self):
        unity = self.ws.cfg.unity
        for name in ("Crate", "Stack"):
            self.assertTrue((unity / f"Assets/FromGodot/{name}.prefab").is_file())
            self.assertIn("PrefabImporter", (unity / f"Assets/FromGodot/{name}.prefab.meta").read_text())
        self.assertTrue((unity / "Assets/FromGodot.meta").is_file())
        for res in ("res://scenes/crate.tscn", "res://scenes/stack.tscn", "res://scenes/prefabs.tscn"):
            assert_same_layout(self, self.ws.u(res), self.ws.g(res))

    def test_placed_scenes_become_prefab_instances(self):
        level_text = (self.ws.cfg.unity / "Assets/FromGodot/Level.unity").read_text()
        crate_guid = UnityProject(self.ws.cfg.unity).guid_of("Assets/FromGodot/Crate.prefab")
        self.assertIn(f"m_SourcePrefab: {{fileID: 100100000, guid: {crate_guid}, type: 3}}", level_text)
        self.assertIn("m_AddedGameObjects:\n    - targetCorrespondingSourceObject:", level_text)  # the Sticker
        u = by_path(self.ws.u("res://scenes/prefabs.tscn"))
        self.assertEqual(u["Shelf/Crate On Shelf/Sticker"].get("parent"), u["Shelf/Crate On Shelf"].key)
        self.assertTrue(all(x.empty for x in self.ws.sync_all()))


class Discovery(unittest.TestCase):
    def test_godot_scenes_placed_elsewhere_are_prefabs(self):
        ws = Workspace()
        try:
            (ws.root / "twinscene.json").write_text(json.dumps({"shared": SHARED}))
            pairs = {p.godot: p for p in S.scene_pairs(Config.load(ws.root))}
            self.assertEqual(pairs["res://scenes/crate.tscn"].kind, "prefab")
            self.assertEqual(pairs["res://scenes/crate.tscn"].unity, "Assets/Prefabs/Crate.prefab")  # paired by name
            self.assertEqual(pairs["res://scenes/prefabs.tscn"].kind, "scene")
            order = [p.name for p in S.scene_pairs(Config.load(ws.root))]
            self.assertLess(order.index("Crate.prefab"), order.index("Stack.prefab"))
        finally:
            ws.close()


if __name__ == "__main__":
    unittest.main()


class Overrides(unittest.TestCase):
    """Edits inside placed prefabs: a red, brighter, raised lid; a hidden lid; another material (both fixtures)."""

    def lid_fields(self, scene, path):
        n = by_path(scene)[path]
        return {k.rsplit(":", 1)[1]: v for k, v in n.fields.items() if k.startswith("o:")}

    def test_unity_overrides_reach_godot_and_come_back(self):
        ws = PrefabWorkspace(UNITY_TO_GODOT)
        try:
            ws.sync_all()
            level = "res://levels/level.tscn"
            g = ws.g(level)
            big = self.lid_fields(g, "Crate Big")
            self.assertTrue(same(big["light.color"], (1, 0.1, 0.1)) and big["light.energy"] == 3.0)
            self.assertTrue(same(big["position"], (0, 0.8, 0)))
            self.assertEqual(self.lid_fields(g, "Crate"), {"active": False})
            self.assertEqual(len(self.lid_fields(g, "Shelf/Crate On Shelf")["materials"]), 1)
            text = ws.gtext(level)
            self.assertIn('[node name="Lid" parent="Crate Big"', text)
            self.assertIn('[editable path="Crate Big"]', text)
            self.assertTrue(all(x.empty for x in ws.sync_all()))

            # Godot changes the lid's brightness and un-hides the other lid
            f = ws.cfg.godot / level[6:]
            doc = tscn.load(f)
            ed = gw.GodotEditor(doc, GodotProject(ws.cfg.godot, ws.cfg.shared_godot), level, random.Random(2))
            n = by_path(gr.read(doc, ed.project, level))
            k = next(k for k in n["Crate Big"].fields if k.endswith(":light.energy"))
            ed.apply(n["Crate Big"].key, {k: 5.0}, dict(n["Crate Big"].fields, **{k: 5.0}))
            k2 = next(k for k in n["Crate"].fields if k.endswith(":active"))
            ed.apply(n["Crate"].key, {k2: None}, {kk: v for kk, v in n["Crate"].fields.items() if kk != k2})
            ed.finish()
            tscn.save(doc, f)
            r = ws.sync_all()
            self.assertEqual(sum(len(x.conflicts) for x in r), 0)
            unity_text = (ws.cfg.unity / "Assets/Scenes/Prefabs.unity").read_text()
            self.assertIn("propertyPath: m_Intensity\n      value: 5", unity_text)
            u = ws.u(level)
            self.assertEqual(self.lid_fields(u, "Crate Big")["light.energy"], 5.0)
            self.assertEqual(self.lid_fields(u, "Crate"), {})  # the hidden-lid edit is gone in Unity too
            self.assertTrue(all(x.empty for x in ws.sync_all()))
        finally:
            ws.close()

    def test_godot_overrides_reach_unity(self):
        ws = PrefabWorkspace(GODOT_TO_UNITY)
        try:
            ws.sync_all()
            u = ws.u("res://scenes/prefabs.tscn")
            big = self.lid_fields(u, "Crate Big")
            self.assertTrue(same(big["light.color"], (1, 0.1, 0.1)) and big["light.energy"] == 3.0)
            self.assertEqual(self.lid_fields(u, "Crate"), {"active": False})
            shelf = self.lid_fields(u, "Shelf/Crate On Shelf")
            self.assertTrue(shelf["materials"][0].endswith("Green.mat"))
            self.assertTrue(same(by_path(u)["Shelf/Crate On Shelf"].get("position"), (0, 2, 0)))
            self.assertTrue(all(x.empty for x in ws.sync_all()))
        finally:
            ws.close()

    def test_conflicting_override_is_reported_and_resolvable(self):
        ws = PrefabWorkspace(UNITY_TO_GODOT)
        try:
            ws.sync_all()
            level = "res://levels/level.tscn"
            upath = ws.cfg.unity / "Assets/Scenes/Prefabs.unity"
            ut = upath.read_text()
            upath.write_text(ut.replace("propertyPath: m_Intensity\n      value: 3", "propertyPath: m_Intensity\n      value: 4"))
            f = ws.cfg.godot / level[6:]
            f.write_text(f.read_text().replace("light_energy = 3.0", "light_energy = 6.0"))
            r = ws.sync_all()
            c = [c for x in r for c in x.conflicts]
            self.assertEqual(len(c), 1)
            self.assertTrue(c[0].field.endswith(":light.energy"))
            self.assertEqual((c[0].unity, c[0].godot), (4.0, 6.0))
            r = S.sync(ws.cfg, rng=ws.rng, resolve={(c[0].unity_key, c[0].field): "godot"})
            self.assertEqual(sum(len(x.conflicts) for x in r), 0)
            self.assertEqual(self.lid_fields(ws.u(level), "Crate Big")["light.energy"], 6.0)
        finally:
            ws.close()

    def test_two_prefabs_with_the_same_file_name_keep_separate_state(self):
        ws = PrefabWorkspace(UNITY_TO_GODOT + GODOT_TO_UNITY)  # Assets/Prefabs/Crate.prefab and Assets/FromGodot/Crate.prefab
        try:
            names = [p.name for p in S.scene_pairs(ws.cfg)]
            self.assertEqual(len(names), len(set(names)))
            ws.sync_all()
            self.assertTrue(same(self.lid_fields(ws.g("res://levels/level.tscn"), "Crate Big")["light.energy"], 3.0))
            self.assertTrue(same(self.lid_fields(ws.u("res://scenes/prefabs.tscn"), "Crate Big")["light.energy"], 3.0))
            self.assertTrue(all(x.empty for x in ws.sync_all()))
        finally:
            ws.close()
