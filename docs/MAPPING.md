# How Unity and Godot map onto each other

Facts here were measured with the real editors (Godot 4.7.2, Unity 6.6 / 6000.6.4f1) unless marked *assumed*.
The test projects in `tests/projects/` hold the evidence.

## Axes

| | Unity | Godot / TwinScene |
|---|---|---|
| Handedness | left | right |
| Up | +Y | +Y |
| Camera and light look along | +Z | −Z |
| Model files (.obj measured) | importer mirrors X | imported as written |

Conversion (`twinscene/coords.py`): position `(x, y, z) → (−x, y, z)`, rotation `(x, y, z, w) → (x, −y, −z, w)`,
scale unchanged. Shared model files line up with no extra rotation. Cameras, directional lights and spot lights
get a 180° turn about Y, and their children get the inverse so they stay put.

## Object identity

| | Unity | Godot |
|---|---|---|
| Permanent ID | GameObject `fileID` (`--- !u!1 &<id>`) | `unique_id=` in the node header (Godot 4.6+) |
| Rename / move | ID kept | ID kept (measured) |
| Duplicate | new ID | new ID (measured) |
| ID written by another tool | kept | kept through load and save (measured) |

TwinScene pairs the two IDs in `sync/`; it doesn't need IDs of its own.

## Objects

| TwinScene field | Unity | Godot |
|---|---|---|
| name, parent, order | GameObject `m_Name`, Transform `m_Father` / `m_Children`, `SceneRoots` | node header, file order |
| active | `m_IsActive` | `visible` |
| mesh: primitive | built-in mesh fileIDs 10202 cube, 10207 sphere, 10208 capsule, 10206 cylinder, 10209 plane (10×10), 10210 quad | `BoxMesh`, `SphereMesh`, `CapsuleMesh`, `CylinderMesh`, `PlaneMesh`, `QuadMesh` sub-resources |
| mesh: asset | MeshFilter guid, or a PrefabInstance of a model file | `MeshInstance3D.mesh` ext_resource, or an instanced scene |
| materials | MeshRenderer `m_Materials` | `material_override`, `surface_material_override/N`, the mesh's `material` |
| camera | Camera `field of view` (vertical), `near/far clip plane`, `orthographic size` (half height) | `Camera3D` `fov` (vertical by default), `near`, `far`, `size` (full height) |
| light | Light `m_Type` 0 spot / 1 directional / 2 point, `m_Intensity`, `m_Range`, `m_SpotAngle` (full cone) | `DirectionalLight3D`, `OmniLight3D` (`omni_range`), `SpotLight3D` (`spot_range`, `spot_angle` = half cone), `light_energy` |
| collider | Box/Sphere/CapsuleCollider on the GameObject, with `m_Center` | `CollisionShape3D` node (its own transform) |
| body | Rigidbody (`m_IsKinematic`); colliders without one are static | `RigidBody3D`, `StaticBody3D`, `CharacterBody3D` |

Godot leaves properties at their default out of the file; `godot/reader.py` holds the defaults it needs.

## Prefabs

| | Unity | Godot |
|---|---|---|
| The reusable thing | `.prefab` file, one root object | a `.tscn` scene placed in other scenes (its root is the object) |
| A placed copy | `PrefabInstance` with overrides aimed at the prefab's own object ids, plus a "stripped" stand-in Transform | a node with `instance=ExtResource(...)` (Godot 4.7 still writes `type=` on it) |
| A placed copy's id | the PrefabInstance's fileID | its `unique_id` (it can repeat the source scene root's id; ids only need to be unique per file) |
| Objects added under a placed copy | listed in `m_AddedGameObjects`, parented to the stand-in Transform | ordinary nodes whose parent path runs through the instance |
| Edits inside a placed copy | `m_Modifications` aimed at inner objects | sections without `type` under the instance |

TwinScene pairs prefabs with scenes by name (`Crate.prefab` <-> `crate.tscn`); a Godot scene placed in another scene
counts as a prefab. Prefabs sync before the scenes that use them, innermost first. A placed copy syncs which prefab
it is, its name, transform, visibility and parent.

**Edits inside a placed copy** (overrides) sync too: an inner object's position, rotation, scale, visibility, light
colour, brightness and range, and materials (including the placed object's own material). They're fields named
`o:<inner object key>:<field>`, translated through the prefab pair's ID table; only values that differ from the
prefab's own count. In Godot they need `[editable path="..."]` ("Editable Children"), which TwinScene adds. Not yet:
renaming inner objects, other properties, objects removed from or added deep inside a placed copy, overrides on
objects of prefabs nested inside the placed prefab, and prefab variants (inherited scenes).

## Model files (FBX, glTF, OBJ)

| | Unity | Godot |
|---|---|---|
| An object showing one mesh of a model | MeshFilter `{fileID, guid}`; the model's `.meta` names the ids (`fileIDToRecycleName`, or `internalIDToNameTable` in newer files) | a MeshInstance3D with a mesh Godot saves out of the model (`.import` > `_subresources` > `meshes` > *name* > `save_to_file`), so it gets its own materials |
| Mesh names | from the `.meta` | the same names (measured on ML-Agents' FBX files) |
| Units | baked into the mesh: FBX `UnitScaleFactor` / 100 x `globalScale` | kept in the file's units; TwinScene scales the mesh helper to match |
| Pivots | the mesh is always centred on its FBX `ScalingPivot` | the same when `RotationPivot` = `ScalingPivot`; otherwise the vertices stay where the file has them, so TwinScene moves the mesh helper by −`ScalingPivot` (read from the FBX) |
| A whole model placed as is | a PrefabInstance of the model; glTF needs the glTFast package, whose ids TwinScene can't compute, so it goes in a mesh helper without overrides | an instance of the model scene |

TwinScene writes a model's complete `.import` when Godot hasn't imported it yet, with `fbx/importer=0` (Godot's
built-in importer): without it Godot uses the external FBX2glTF converter, usually missing, and imports nothing.

## Materials

| TwinScene | Unity Standard | Unity URP Lit | Godot StandardMaterial3D |
|---|---|---|---|
| albedo | `_Color` | `_BaseColor` | `albedo_color` |
| albedo_texture | `_MainTex` | `_BaseMap` | `albedo_texture` |
| metallic | `_Metallic` | `_Metallic` | `metallic` |
| roughness | 1 − `_Glossiness` | 1 − `_Smoothness` | `roughness` |

*Assumed:* light intensity 1:1 between Unity's built-in pipeline and Godot. URP/HDRP physical light units will need
their own conversion.

## Scripts (paired)

| | Unity | Godot |
|---|---|---|
| A script on an object | a MonoBehaviour whose `m_Script` is the script's guid | `script = ExtResource(...)` on the node, or on a helper child node (`metadata/twinscene_part = "script:<Name>"`) when the node's type can't hold it (Godot only attaches a script to the class it extends) |
| Its settings | public or `[SerializeField]` fields, all saved in the scene | `@export` vars / `[Export]` members; only values that differ from the script's default are saved, so TwinScene reads the defaults from the script |
| A setting changed on one placed prefab | `m_Modifications`: `speed`, or `axis.x`, `axis.y`, `axis.z` for a vector | the property on the instance (or the edited inner node) |
| Vector3 settings | mirrored like positions (*assumed*: script vectors are positions, offsets or directions) | as is |

Scripts are paired by file name written the same way, or in `twinscene.json`. A script whose base class (`extends`)
is a physics body or a visual node can only go on a node of that type; TwinScene reports it instead of guessing a
different node type.

## Known gaps

- A Unity GameObject can hold several components that Godot splits into separate nodes (a mesh and a collider,
  for example). New objects get helper child nodes marked `metadata/twinscene_part`; existing ones are patched in
  place.
- Unity capsules along X or Z, mesh colliders, area lights, and multiple colliders per GameObject aren't synced yet.
- Unity Quad faces −Z, Godot QuadMesh faces +Z (*assumed*, to verify).
- Prefab variants / inherited scenes; the override gaps listed under Prefabs.
- Pivots are read from binary FBX files only (ASCII FBX meshes with unequal pivots would sit offset in Godot).
- Unity models imported with Unity 2019.3+ don't list mesh names in their `.meta`; such objects place the whole
  model in Godot.
- Godot convex/concave polygon collision shapes. (Cylinders become a convex mesh collider with Unity's cylinder mesh
  in a helper; an endless WorldBoundary plane becomes a 10 km box in Unity, and Godot keeps its plane.)
- Binary Godot scenes (`.scn`) can't be read; save them as `.tscn`.
- Script settings that hold references (other objects, assets), arrays, dictionaries and enums aren't synced.
- Godot C# scripts are read from their source; Godot .NET builds aren't needed for syncing.
- UI isn't synced (Unity Canvas/RectTransform objects, Godot Control and 2D nodes), nor objects hidden from Unity's
  Hierarchy (editor internals).
