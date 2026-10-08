# Changelog

## 0.1.0 (first public release, alpha)

Two-way sync between a Unity project and a Godot project, as pull requests or from the command line.

- **Scenes and prefabs.** Objects (names, parents, order, shown/hidden, transforms), Unity prefabs as Godot scenes,
  nested prefabs, objects added under placed prefabs, and edits inside placed prefabs (transforms, visibility,
  lights, materials, script settings).
- **Content.** Built-in meshes, FBX/glTF/OBJ models (single meshes out of a model too, with unit scale and pivots),
  PBR materials, cameras, directional/point/spot lights, box/sphere/capsule/cylinder colliders (several per object),
  triggers, rigid bodies. Models, textures and audio are mirrored between the projects.
- **Scripts.** Unity C# scripts pair with Godot scripts (GDScript or C#) by name or in `twinscene.json`; attached
  scripts and their shared inspector settings sync. `twinscene scripts` lists the pairs.
- **Safe merging.** Each engine is compared with its own last-synced state; a field changed on both sides is a
  conflict, never a guess. Everything TwinScene doesn't understand is left as it was.
- **GitHub Action** that opens a sync pull request on push.
- **Editor add-ons** for Unity 6 and Godot 4.6+: Sync button, change labels, conflict resolver, claims.
- Checked inside the real editors on Unity's ML-Agents examples (2,053 objects, all matching) and three Godot demos.

Known gaps: UI, animation, prefab variants / inherited scenes, shaders and particles, terrain, mesh colliders,
binary `.scn` scenes, and script settings that reference objects, arrays or enums. See docs/MAPPING.md.
