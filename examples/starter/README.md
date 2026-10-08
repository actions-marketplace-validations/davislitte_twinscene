# TwinScene starter

A small level made in Unity: crates (a prefab), a stack of crates (a prefab inside a prefab), a shelf, lamps and a
camera. Every crate carries a `Spinner` script, and the big crate spins faster than the others. The Godot project
next to it is empty, apart from `scripts/spinner.gd`, the Godot partner of `Spinner.cs`.

Try it in five minutes:

1. Copy this folder somewhere outside the TwinScene repository (or use it in place) and install TwinScene:

   ```bash
   pip install twinscene
   ```

   (Before the first PyPI release: `pip install git+https://github.com/davislitte/twinscene`.)

2. Build the Godot side:

   ```bash
   cd starter
   twinscene sync
   ```

   The report lists what was added. `godot/scenes/Level.tscn` and the crate scenes in `godot/prefabs/` are new.

3. Open `godot/` in Godot 4.6 or newer and run `scenes/Level.tscn`. The same level, with the same crates spinning;
   the big one still spins faster (`speed = 120` on that crate only).

4. Open `unity/` in Unity 6 and the `Level` scene. Change something: move a crate, or set the big crate's Spinner
   speed to 300. Save, then run `twinscene sync` again. Only that change goes to Godot.

5. Change something in Godot (move the lamp, change a spinner's speed), save, `twinscene sync`: it comes back to Unity.

   `twinscene scripts` shows which scripts are paired and which of their settings sync.

To get the Sync button, change labels and the conflict resolver inside the editors, add the add-ons: see
[docs/editor-addons.md](../../docs/editor-addons.md).
