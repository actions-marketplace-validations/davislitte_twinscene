"""Compares what Unity and Godot report for the same scene (probe_godot.gd, Assets/Editor/Probe.cs).

Godot world space is Unity's mirrored in X. On screen, through each scene's own main camera, both should look the
same, so screen positions must match directly.
    python compare_probes.py unity.json godot.json
"""

import json
import sys

TOL = 1e-3


def mirror(v):
    return [-v[0], v[1], v[2]]


def close(a, b, tol=TOL):
    return len(a) == len(b) and all(abs(x - y) <= tol * max(1.0, abs(x), abs(y)) for x, y in zip(a, b))


def compare(unity: dict, godot: dict) -> list[str]:
    problems = []
    for path in sorted(set(unity) | set(godot)):
        u, g = unity.get(path), godot.get(path)
        if u is None or g is None:
            problems.append(f"{path}: only in {'Godot' if u is None else 'Unity'}")
            continue
        checks = [("position", mirror(u["pos"]), g["pos"])]
        for what, key in (("facing", "look"), ("up", "up")):  # a zero scale leaves Godot no direction to report
            if any(abs(c) > 1e-6 for c in g[key]):
                checks.append((what, mirror(u[key]), g[key]))
        if "screen" in u and "screen" in g:  # (a point right by the camera plane may be "behind" in one engine only)
            checks.append(("on screen", u["screen"], g["screen"]))
        if "light" in u or "light" in g:
            checks.append(("light (color, brightness, range)", u.get("light", []), g.get("light", [])))
        if "bounds" in u or "bounds" in g:
            ub = u.get("bounds")
            if ub:  # mirroring X swaps the box's min and max x
                ub = [-ub[3], ub[1], ub[2], -ub[0], ub[4], ub[5]]
            checks.append(("mesh bounds", ub or [], g.get("bounds", [])))
        for what, a, b in checks:
            if not close(a, b):
                problems.append(f"{path}: {what} Unity {fmt(a)} vs Godot {fmt(b)}")
        if u["active"] != g["active"]:
            problems.append(f"{path}: active Unity {u['active']} vs Godot {g['active']}")
    return problems


def fmt(v):
    return "(" + ", ".join(f"{c:.3f}" for c in v) + ")"


def main(argv):
    unity = json.load(open(argv[1], encoding="utf-8"))
    godot = json.load(open(argv[2], encoding="utf-8"))
    problems = compare(unity, godot)
    print(f"{len(set(unity) & set(godot))} objects in both")
    for p in problems:
        print("  MISMATCH", p)
    print("all match" if not problems else f"{len(problems)} mismatches")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
