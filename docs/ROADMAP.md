# TwinScene roadmap

TwinScene keeps one game in two engines. One developer works in Unity, another in Godot, and GitHub carries each
side's changes to the other as a pull request.

Status: **alpha** (0.1.0). Items marked done are usable and checked in both real editors.

## The idea in one picture

```
 Unity dev                                  Godot dev
    │ push                                     │ push
    ▼                                          ▼
 unity/  ──► GitHub Action: twinscene sync ◄── godot/
                 │  read both sides
                 │  diff each against the last synced state (sync/state/)
                 │  patch only the fields TwinScene understands
                 ▼
          pull request "Sync from Unity" / "Sync from Godot"
```

## Design rules

1. **Patch, don't regenerate.** When syncing, the writers change only the fields TwinScene understands and leave every
   other line of the scene file as it was. Engine-only data survives the round trip.
2. **Use the engines' own IDs.** Unity: the GameObject's fileID. Godot 4.6+: the node's `unique_id`. Both survive
   renames and moves and change on duplicate (measured), so `sync/` only needs a table pairing them.
3. **Three-way merge, each side against itself.** `sync/state/` keeps each engine's own last-synced reading of every
   scene. Changes are found by comparing an engine with its own snapshot, never Unity data with Godot data, so imperfect
   translation can't show up as false changes. A field changed on only one side is copied over. A field changed differently on both sides is a conflict, and it's
   listed in the pull request rather than guessed.
4. **Never silent.** Every sync arrives as a pull request a person merges.
5. **No engine needed in CI.** The converter reads and writes text files only. Unity scenes must use text
   serialization (Unity's default, "Force Text").
6. **Coordinates live in one place.** Unity is left-handed (Y up), Godot is right-handed (Y up, cameras look down -Z).
   All conversion goes through `twinscene/coords.py` and is verified against both real editors.

## Repository layout

```
twinscene/            Python package: the converter and sync engine (stdlib only)
  model.py            neutral scene model
  coords.py           Unity <-> Godot axis conversion
  unity/              Unity YAML reader + in-place patcher + writer
  godot/              .tscn reader + in-place patcher + writer
  sync.py             diff, three-way merge, conflicts
  scripts.py          Unity <-> Godot script pairing
  cli.py              `twinscene sync`, `status`, `scripts`, `claims`, ...
addons/unity/         Unity editor package (C#), see docs/editor-addons.md
addons/godot/         Godot editor plugin (GDScript)
action/               GitHub Action
examples/starter/     a small project to try TwinScene on
tests/projects/       projects and scenes saved by the real editors
tests/tools/          checks that run inside the real editors
docs/                 this file, MAPPING.md (what maps to what)
```

## Milestones

### M0: groundwork
- [x] Repo, MIT license, plan
- [x] Test fixtures saved by real Godot 4.7 and real Unity 6.6
- [x] Verify axis conversion with a reference model in both editors (see MAPPING.md)

### M1: readers
- [x] Unity scene YAML parser that remembers where every value is in the file (for patching)
- [x] Godot `.tscn` parser, same
- [x] Both read into the neutral model: hierarchy, names, active/visible, transforms, mesh references, lights, cameras,
      box/sphere/capsule colliders, rigidbodies, standard (PBR) materials

### M2: one-way converter, release v0.1
- [x] Unity -> Godot: a scene missing on one side is built from the other (`twinscene sync`), checked in both
      real editors with tests/tools/probe_*
- [x] Godot -> Unity, the same
- [x] Shared assets (models, textures, audio) are mirrored to both projects
- [ ] Unsupported things become placeholders (they're listed in the report already)

### M3: two-way sync engine
- [x] ID map and last-synced state in `sync/`
- [x] Diff and three-way merge, conflict report
- [x] In-place patchers for both formats (design rule 1)
- [x] `twinscene sync` on the command line

### M4: GitHub Action, release v0.2
- [x] Runs on push, opens or updates one sync pull request (titled by direction)
- [x] Never re-syncs its own commits (no loops)
- [x] Conflicts listed in the pull request description

### M5: editor add-ons, part 1
- [x] ~~Sync IDs stamped on create~~ not needed: the engines' own IDs already behave this way (measured)
- [x] TwinScene's helper objects explain themselves and warn when edited
- [x] "synced" / "this engine only" in the inspector

### M6: editor add-ons, part 2, release v0.3
- [x] Claims ("I'm working on this"), shared through the repository's git remote
- [x] Warning when editing something the other side changed, claimed or in conflict
- [x] "Sync now" button
- [x] Side-by-side conflict resolver (window/dock and inspector)

### Later
- [x] Prefabs <-> instanced scenes, nested prefabs, objects added under placed prefabs (checked in both editors)
- [x] Edits inside placed prefabs (overrides): transform, visibility, lights, materials (checked in both editors)
- [ ] Prefab variants / inherited scenes; more override properties
- [x] Script pairing: partner scripts attached, shared inspector settings synced, also per placed prefab (checked in
      both editors: tests/tools/check_scripts_in_engines.py)
- [ ] Script settings that reference other objects or assets; arrays; enums
- UI (Canvas <-> Control nodes)
- Animation clips
- Shared C# gameplay code template (Godot .NET)
- Unreal, through an editor-side Python bridge

## Tested on real projects

| Project | Direction | Result |
|---|---|---|
| Unity ML-Agents examples (Unity 6.0, 8 scenes, 32 prefabs, 13 FBX models) | Unity -> Godot | 2,053 objects compared in both editors: positions, facing, on-screen placement and mesh bounds all match (FBX pivots included); re-sync changes nothing |
| Godot demos: squash_the_creeps, platformer, physics_tests | Godot -> Unity | 805 objects compared in both editors: physics_tests all match; squash_the_creeps all but an animated glTF's rest pose; platformer all but binary `.scn` scenes (unreadable) and sphere tessellation |

Run it yourself with `tests/tools/check_real_project.py`.

## Prior art

- [Unidot Importer](https://github.com/V-Sekai/unidot_importer): Unity to Godot, one way, MIT. Worth learning from,
  and code can be shared because the licenses match.
- PuroCode and Relative Games exporters: paid, one way.
- GodotWithU: live collaboration inside Godot only.
- NVIDIA Omniverse connectors: OpenUSD, mostly one way, aimed at film and visualization.
