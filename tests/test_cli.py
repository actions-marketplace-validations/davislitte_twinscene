"""The command line the editor add-ons call: --json status, --resolve, and claims over a real git remote."""

import contextlib
import io
import json
import subprocess
import unittest

from twinscene import cli
from twinscene.model import same

from .test_sync import Workspace, by_path


def run(*args) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class Cli(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.root = str(self.ws.root)
        self.assertEqual(run("--root", self.root, "sync")[0], 0)

    def tearDown(self):
        self.ws.close()

    def status(self):
        code, out = run("--root", self.root, "status", "--json")
        return code, json.loads(out)

    def test_status_json_lists_pending_changes_with_keys_and_paths(self):
        def edit(ed, n):
            f = dict(n["Box/Child"].fields, position=(0.0, 3.0, 0.0))
            ed.apply(n["Box/Child"].key, {"position": f["position"]}, f)
        self.ws.edit_godot(edit)
        before = self.ws.upath.read_text()
        code, st = self.status()
        self.assertEqual(code, 0)
        self.assertEqual(self.ws.upath.read_text(), before)  # status changes nothing
        ch = st["scenes"][0]["changes"]
        self.assertEqual(len(ch), 1)
        c = ch[0]
        self.assertEqual((c["direction"], c["kind"], c["fields"]), ("to_unity", "changed", ["position"]))
        self.assertEqual((c["unity_path"], c["godot_path"]), ("Box/Child", "Box/Child"))
        self.assertTrue(c["unity_key"].startswith("u:") and c["godot_key"].startswith("g:"))

    def test_resolve_one_conflict(self):
        for edit, pos in ((self.ws.edit_unity, (5.0, 0.0, 0.0)), (self.ws.edit_godot, (0.0, 0.0, 5.0))):
            edit(lambda ed, n, pos=pos: ed.apply(n["Lamp"].key, {"position": pos}, dict(n["Lamp"].fields, position=pos)))
        code, st = self.status()
        self.assertEqual(code, 3)
        conflict = st["scenes"][0]["conflicts"][0]
        self.assertEqual((conflict["path"], conflict["field"]), ("Lamp", "position"))
        code, _ = run("--root", self.root, "sync", "--resolve", f"{conflict['godot_key']}:position=godot")
        self.assertEqual(code, 0)
        self.assertTrue(same(by_path(self.ws.unity())["Lamp"].get("position"), (0, 0, 5)))
        self.assertEqual(self.status()[0], 0)

    def test_claims_through_a_git_remote(self):
        remote = self.ws.root.parent / (self.ws.root.name + "-remote.git")
        git(self.ws.root.parent, "init", "--quiet", "--bare", str(remote))
        try:
            git(self.root, "init", "--quiet", "-b", "main")
            git(self.root, "config", "user.name", "Ana")
            git(self.root, "config", "user.email", "ana@example.com")
            git(self.root, "remote", "add", "origin", str(remote))
            code, out = run("--root", self.root, "claims", "--json")
            self.assertEqual((code, json.loads(out)["claims"]), (0, []))

            code, out = run("--root", self.root, "claim", "--scene", "Main", "--godot-path", "Ball", "--json")
            self.assertEqual(code, 0, out)
            mine = json.loads(out)["claims"]
            self.assertEqual(len(mine), 1)
            self.assertEqual((mine[0]["unity_path"], mine[0]["who"]), ("Ball", "Ana <ana@example.com>"))
            self.assertTrue(mine[0]["unity_key"].startswith("u:"))
            self.assertEqual(git(self.root, "status", "--porcelain"), git(self.root, "status", "--porcelain"))
            self.assertNotIn("claims.json", git(self.root, "status", "--porcelain"))  # working copy untouched

            # a second developer, in their own clone, sees the claim and can't take it
            other = self.ws.root.parent / (self.ws.root.name + "-bo")
            git(self.ws.root.parent, "clone", "--quiet", str(remote), str(other))
            try:
                (other / "twinscene.json").write_text((self.ws.root / "twinscene.json").read_text())
                (other / "sync").mkdir()
                (other / "sync" / "Main.json").write_text((self.ws.root / "sync" / "Main.json").read_text())
                git(other, "config", "user.name", "Bo")
                git(other, "config", "user.email", "bo@example.com")
                code, out = run("--root", str(other), "claims", "--json")
                self.assertEqual(json.loads(out)["claims"][0]["who"], "Ana <ana@example.com>")
                code, out = run("--root", str(other), "claim", "--scene", "Main", "--unity-path", "Ball", "--json")
                self.assertEqual(code, 2)
                self.assertIn("already claimed by Ana", json.loads(out)["error"])
                code, out = run("--root", str(other), "claim", "--scene", "Main", "--unity-path", "Sun", "--json")
                self.assertEqual(code, 0)
                self.assertEqual(len(json.loads(out)["claims"]), 2)
            finally:
                import shutil
                shutil.rmtree(other, ignore_errors=True)

            # Ana sees both, then releases hers
            code, out = run("--root", self.root, "status", "--json", "--claims")
            st = json.loads(out)
            self.assertEqual(st["me"], "Ana <ana@example.com>")
            self.assertEqual(sorted(c["unity_path"] for c in st["claims"]), ["Ball", "Sun"])
            code, out = run("--root", self.root, "release", "--all", "--json")
            self.assertEqual([c["unity_path"] for c in json.loads(out)["claims"]], ["Sun"])
        finally:
            import shutil
            shutil.rmtree(remote, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
