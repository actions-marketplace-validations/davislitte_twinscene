"""The starter example (examples/starter) does what its README says."""

import shutil
import tempfile
import unittest
from pathlib import Path

from twinscene import sync as S
from twinscene.godot import tscn
from twinscene.project import Config

STARTER = Path(__file__).resolve().parents[1] / "examples" / "starter"


class Starter(unittest.TestCase):
    def test_first_sync_builds_the_godot_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "starter"
            shutil.copytree(STARTER, root)
            cfg = Config.load(root)
            reports = S.sync(cfg)
            self.assertFalse(any(r.conflicts for r in reports))
            level = tscn.load(root / "godot" / "scenes" / "Level.tscn")
            big = next(s for s in level.nodes() if s.get("name") == "Crate Big")
            self.assertEqual(big.value("speed"), 120.0)  # the one crate that spins faster
            crate = tscn.load(root / "godot" / "prefabs" / "Crate.tscn")
            self.assertIn("res://scripts/spinner.gd", [e.get("path") for e in crate.by_tag("ext_resource")])
            self.assertTrue(all(r.empty for r in S.sync(Config.load(root))))  # and a second sync has nothing to do


if __name__ == "__main__":
    unittest.main()
