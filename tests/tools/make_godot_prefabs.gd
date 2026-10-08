# Builds instanced-scene fixtures with the real Godot: scenes/crate.tscn (a box with a lid light), scenes/stack.tscn
# (two crates, nested), and scenes/prefabs.tscn placing them in different ways.
# Run: godot --headless --path tests/projects/godot_ref --script <this file>
extends SceneTree


func _save(root: Node, path: String) -> PackedScene:
	var packed := PackedScene.new()
	var err := packed.pack(root)
	if err == OK:
		err = ResourceSaver.save(packed, path)
	print("make_godot_prefabs: ", path, " ", error_string(err))
	root.free()
	return load(path)


func _own(node: Node, parent: Node, root: Node) -> Node:
	parent.add_child(node)
	node.owner = root
	return node


func _place(scene: PackedScene, parent: Node, root: Node, name: String) -> Node3D:
	var n: Node3D = scene.instantiate()
	n.name = name
	parent.add_child(n)
	n.owner = root
	return n


func _init() -> void:
	var crate := MeshInstance3D.new()
	crate.name = "Crate"
	var box := BoxMesh.new()
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.2, 0.5, 0.9)
	box.material = mat
	crate.mesh = box
	var lid := OmniLight3D.new()
	lid.name = "Lid"
	lid.position = Vector3(0, 0.6, 0)
	lid.rotation_degrees = Vector3(0, 0, 15)
	lid.omni_range = 3.0
	lid.light_color = Color(1, 0.8, 0.3)
	_own(lid, crate, crate)
	var crate_scene := _save(crate, "res://scenes/crate.tscn")

	var stack := Node3D.new()
	stack.name = "Stack"
	_place(crate_scene, stack, stack, "Bottom")
	var top := _place(crate_scene, stack, stack, "Top")
	top.position = Vector3(0, 1, 0)
	top.rotation_degrees = Vector3(0, 45, 0)
	var stack_scene := _save(stack, "res://scenes/stack.tscn")

	var level := Node3D.new()
	level.name = "Prefabs"
	var cam := Camera3D.new()
	cam.name = "Main Camera"
	cam.position = Vector3(0, 4, 12)
	cam.rotation_degrees = Vector3(-12, 0, 0)
	_own(cam, level, level)
	var c1 := _place(crate_scene, level, level, "Crate")
	c1.position = Vector3(2, 0.5, 1)
	c1.rotation_degrees = Vector3(0, 30, 0)
	var c2 := _place(crate_scene, level, level, "Crate Big")
	c2.position = Vector3(-3, 1, 0)
	c2.scale = Vector3(2, 2, 2)
	var shelf := Node3D.new()
	shelf.name = "Shelf"
	shelf.position = Vector3(0, 0, -4)
	_own(shelf, level, level)
	var c3 := _place(crate_scene, shelf, level, "Crate On Shelf")
	c3.position = Vector3(0, 2, 0)
	var sticker := Node3D.new()  # added to an instance, in this scene only
	sticker.name = "Sticker"
	sticker.position = Vector3(0, 0, 0.51)
	_own(sticker, c3, level)
	var s1 := _place(stack_scene, level, level, "Stack")
	s1.position = Vector3(5, 0, 2)
	# Edits inside placed scenes (overrides): a red, brighter, raised lid; a hidden lid; another material.
	level.set_editable_instance(c2, true)  # (what "Editable Children" does in the editor)
	level.set_editable_instance(c1, true)
	var big_lid: OmniLight3D = c2.get_node("Lid")
	big_lid.position = Vector3(0, 0.8, 0)
	big_lid.light_color = Color(1, 0.1, 0.1)
	big_lid.light_energy = 3.0
	c1.get_node("Lid").visible = false
	var green := StandardMaterial3D.new()
	green.resource_name = "Green"
	green.albedo_color = Color(0.2, 0.8, 0.3)
	ResourceSaver.save(green, "res://scenes/green.tres")
	(c3 as MeshInstance3D).set_surface_override_material(0, load("res://scenes/green.tres"))
	_save(level, "res://scenes/prefabs.tscn")
	quit()
