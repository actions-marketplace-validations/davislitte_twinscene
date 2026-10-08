"""Claims: "I'm working on this", shared through the game's own git remote.

Claims live in claims.json on a branch of their own (twinscene-claims). They're written with git's plumbing
commands, so the developer's working copy, index and current branch are never touched, and they use the same login
as every other git push. If two people claim at once, the later push is rejected and simply retried.
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
from pathlib import Path

BRANCH = "twinscene-claims"
FILE = "claims.json"
TRACKING = f"refs/remotes/{{remote}}/{BRANCH}"


class ClaimError(RuntimeError):
    pass


def _git(root: Path, *args: str, input: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    # Bytes, not text mode: on Windows text mode would turn "\n" into "\r\n" on the way into git.
    raw = subprocess.run(["git", *args], cwd=root, input=None if input is None else input.encode("utf-8"),
                         capture_output=True)
    p = subprocess.CompletedProcess(raw.args, raw.returncode, raw.stdout.decode("utf-8", "replace"),
                                    raw.stderr.decode("utf-8", "replace"))
    if check and p.returncode != 0:
        raise ClaimError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p


def who(root: Path) -> str:
    name = _git(root, "config", "user.name", check=False).stdout.strip()
    email = _git(root, "config", "user.email", check=False).stdout.strip()
    if not name and not email:
        raise ClaimError("set your name first: git config user.name \"Your Name\"")
    return f"{name} <{email}>" if email else name


def _tracking(remote: str) -> str:
    return TRACKING.format(remote=remote)


def fetch(root: Path, remote: str = "origin") -> tuple[list[dict], str | None]:
    """The current claims and the commit they came from (None if there are none yet). Works offline from the last
    fetch."""
    p = _git(root, "fetch", "--quiet", remote, f"+refs/heads/{BRANCH}:{_tracking(remote)}", check=False)
    if p.returncode != 0 and "couldn't find remote ref" in (p.stderr + p.stdout):
        _git(root, "update-ref", "-d", _tracking(remote), check=False)  # the branch was deleted remotely
    head = _git(root, "rev-parse", "--verify", "--quiet", _tracking(remote), check=False).stdout.strip() or None
    if head is None:
        return [], None
    text = _git(root, "show", f"{head}:{FILE}", check=False).stdout
    try:
        return json.loads(text).get("claims", []), head
    except ValueError:
        return [], head


def _push(root: Path, claims: list[dict], parent: str | None, message: str, remote: str) -> bool:
    body = json.dumps({"claims": claims}, indent=1, sort_keys=True) + "\n"
    blob = _git(root, "hash-object", "-w", "--stdin", input=body).stdout.strip()
    tree = _git(root, "mktree", input=f"100644 blob {blob}\t{FILE}\n").stdout.strip()
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    commit = _git(root, *args).stdout.strip()
    p = _git(root, "push", "--quiet", remote, f"{commit}:refs/heads/{BRANCH}", check=False)
    if p.returncode == 0:
        _git(root, "update-ref", _tracking(remote), commit)
        return True
    if "rejected" in p.stderr or "non-fast-forward" in p.stderr or "fetch first" in p.stderr:
        return False  # someone else pushed first
    raise ClaimError(p.stderr.strip())


def change(root: Path, update, message: str, remote: str = "origin", tries: int = 5) -> list[dict]:
    """Apply update(claims) -> claims and push it, retrying if someone else got there first."""
    for _ in range(tries):
        claims, head = fetch(root, remote)
        new = update([dict(c) for c in claims])
        if new == claims:
            return claims
        if _push(root, new, head, message, remote):
            return new
    raise ClaimError("couldn't update claims: the branch kept changing (try again)")


def claim(root: Path, entry: dict, remote: str = "origin") -> list[dict]:
    """Claim an object. entry: scene, unity_key, godot_key, unity_path, godot_path."""
    me = who(root)

    def update(claims):
        for c in claims:
            if _same_object(c, entry):
                if c["who"] != me:
                    raise ClaimError(f"{entry.get('unity_path') or entry.get('godot_path')} is already claimed by "
                                     f"{c['who']}")
                return claims
        now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
        return claims + [dict(entry, who=me, when=now)]
    return change(root, update, f"claim {entry.get('unity_path') or entry.get('godot_path')}", remote)


def release(root: Path, entry: dict | None = None, remote: str = "origin") -> list[dict]:
    """Release one of my claims, or all of them when entry is None."""
    me = who(root)

    def update(claims):
        return [c for c in claims if not (c["who"] == me and (entry is None or _same_object(c, entry)))]
    return change(root, update, "release " + (entry.get("unity_path") or entry.get("godot_path") or "")
                  if entry else "release all", remote)


def _same_object(a: dict, b: dict) -> bool:
    if a.get("scene") != b.get("scene"):
        return False
    return bool((a.get("unity_key") and a.get("unity_key") == b.get("unity_key")) or
                (a.get("godot_key") and a.get("godot_key") == b.get("godot_key")))
