# Contributing to TwinScene

Thanks for helping. Bug reports from real projects are the most useful thing right now: TwinScene is young, and
every project uses Unity and Godot a little differently.

## Reporting a problem

Open an issue with:

- the Unity and Godot versions,
- what you synced and what came out wrong (the report from `twinscene status` helps),
- if you can, the smallest scene that shows it. Unity scenes must be saved as text (the default).

## Working on the code

TwinScene is plain Python 3.10+ with no dependencies.

```bash
python -m unittest discover -s tests -t .
```

The unit tests run without any engine. Changes to how something is converted should also be checked inside the
real editors with the scripts in `tests/tools/` (they need Unity and Godot installed):

| Script | Checks |
|---|---|
| `check_prefabs_in_engines.py` | prefabs and overrides, both directions |
| `check_scripts_in_engines.py` | script pairing and settings, both directions |
| `check_real_project.py` | any synced repository: every object's place, facing and mesh bounds in both editors |
| `check_unity_addon.py`, `check_godot_addon.py` | the editor add-ons |

## Ground rules

These keep syncs safe; please don't bend them without discussing it first (see docs/ROADMAP.md, "Design rules"):

1. **Patch, don't regenerate.** Writers change only the fields TwinScene understands.
2. **Each engine is compared with its own snapshot**, never with the other engine's data.
3. **Never guess.** If both sides changed something, it's a conflict.
4. **Measure conversions in the real editors**, and write down what was measured (docs/MAPPING.md marks guesses
   as *assumed*).

## Pull requests

Keep them focused, add a test for the behaviour you change, and describe how you checked it.
