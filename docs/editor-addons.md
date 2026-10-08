# Editor add-ons

The add-ons show TwinScene inside each editor. They run the `twinscene` command for everything (so install that
first: `pip install twinscene`), and they need the repository's `twinscene.json` above the project folder.

![The Godot plugin: coloured nodes in the Scene dock, the conflict in the Inspector, and the TwinScene dock](images/godot-addon.png)

## What they do

| | Unity | Godot |
|---|---|---|
| **Sync now**: save, sync both ways, reload the scene | Window > TwinScene | TwinScene dock |
| Changes waiting from the other engine | Window, Hierarchy label, Inspector | Dock, Scene dock colour, Inspector |
| **Conflicts**: both values side by side, pick one | Window and Inspector buttons | Dock and Inspector buttons |
| **Claims**: "I'm working on this", shared through git | Claim / Release buttons, Hierarchy label | Claim / Release buttons, Scene dock colour |
| Warning when you edit something risky | Console + Scene view notice | Editor toast |
| What syncs and what stays engine-only | Inspector header | Inspector |
| TwinScene's helper objects explain themselves | Helper inspector, "helper" label | Inspector, grey in the Scene dock |

Labels, from most to least important: **conflict** (red), **claimed by someone else** (orange), **changed on the
other side** (blue), **claimed by you** (green), **helper** (grey).

## Install

**Unity 6.0 or later** (tested on 6.3 LTS and 6.6): Window > Package Manager > + > *Install package from git URL*:

```
https://github.com/davislitte/twinscene.git?path=/addons/unity
```

**Godot 4.6 or later** (tested on 4.7): copy `addons/godot/addons/twinscene` into your project as
`addons/twinscene`, then turn it on in Project > Project Settings > Plugins.

If the add-on can't find the `twinscene` command, set it in Window > TwinScene > Settings (Unity) or Editor
Settings > `twinscene/command` (Godot), for example `py -3 -m twinscene`.

## Claims

Claims live on a `twinscene-claims` branch of the repository's own git remote, written without touching your
working copy or current branch. They use your normal git login, so there's no token to set up. Two people can't
claim the same object; a claim stays until it's released.

## Tests

Both add-ons are tested inside the real editors:

```bash
python tests/tools/check_unity_addon.py "<path to Unity.exe>" [work folder] [--fresh-settings]
python tests/tools/check_godot_addon.py "<path to Godot console exe>" [work folder]
```

Each builds a repository where the other side has waiting changes, both sides changed the same thing (a conflict) and
another developer holds a claim, then drives the add-on and prints `CHECK PASS/FAIL` lines. `--fresh-settings` lets
an older Unity than the test project's open it. Set `TWINSCENE_SCREENSHOT=<file.png>` for the Godot one to run with a
window and save a screenshot.
