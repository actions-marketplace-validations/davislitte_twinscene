# Builds tests/projects/godot_ref/scenes/reference.tscn with the real Godot, so the fixture is Godot's own output.
# Run: godot --headless --path tests/projects/godot_ref --script ../../tools/make_godot_ref.gd
extends SceneTree


func _own(node: Node, parent: Node, root: Node) -> Node:
	parent.add_child(node)
	node.owner = root
	return node


func _init() -> void:
	var root := Node3D.new()
	root.name = "Reference"

	# A box at a known spot, turned 30 degrees around Y: the axis-conversion check.
	var box := MeshInstance3D.new()
	box.name = "Box"
	var box_mesh := BoxMesh.new()
	box_mesh.size = Vector3(2, 1, 0.5)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.8, 0.2, 0.1)
	mat.metallic = 0.3
	mat.roughness = 0.6
	box_mesh.material = mat
	box.mesh = box_mesh
	box.position = Vector3(1, 2, 3)
	box.rotation_degrees = Vector3(0, 30, 0)
	box.set_meta("twinscene_id", 1001)
	_own(box, root, root)

	# A child with its own offset and non-uniform scale.
	var child := Node3D.new()
	child.name = "Child"
	child.position = Vector3(0, 1, 0)
	child.rotation_degrees = Vector3(10, 20, 30)
	child.scale = Vector3(1, 2, 3)
	_own(child, box, root)

	var cam := Camera3D.new()
	cam.name = "Main Camera"
	cam.position = Vector3(0, 1, 10)
	cam.fov = 60.0
	cam.near = 0.3
	cam.far = 1000.0
	_own(cam, root, root)

	var sun := DirectionalLight3D.new()
	sun.name = "Sun"
	sun.rotation_degrees = Vector3(-50, -30, 0)
	sun.light_energy = 1.2
	sun.light_color = Color(1, 0.95, 0.85)
	sun.shadow_enabled = true
	_own(sun, root, root)

	var lamp := OmniLight3D.new()
	lamp.name = "Lamp"
	lamp.position = Vector3(-2, 3, 0)
	lamp.omni_range = 8.0
	_own(lamp, root, root)

	var spot := SpotLight3D.new()
	spot.name = "Spot"
	spot.position = Vector3(0, 4, -2)
	spot.spot_range = 12.0
	spot.spot_angle = 30.0
	_own(spot, root, root)

	var floor := StaticBody3D.new()
	floor.name = "Floor"
	_own(floor, root, root)
	var floor_shape := CollisionShape3D.new()
	floor_shape.name = "Shape"
	var bs := BoxShape3D.new()
	bs.size = Vector3(20, 1, 20)
	floor_shape.shape = bs
	floor_shape.position = Vector3(0, -0.5, 0)
	_own(floor_shape, floor, root)

	var ball := RigidBody3D.new()
	ball.name = "Ball"
	ball.mass = 2.5
	ball.position = Vector3(0, 5, 0)
	_own(ball, root, root)
	var ball_shape := CollisionShape3D.new()
	ball_shape.name = "Shape"
	var ss := SphereShape3D.new()
	ss.radius = 0.75
	ball_shape.shape = ss
	_own(ball_shape, ball, root)

	var pill := CollisionShape3D.new()
	pill.name = "Pill"
	var cs := CapsuleShape3D.new()
	cs.radius = 0.4
	cs.height = 1.8
	pill.shape = cs
	_own(pill, ball, root)

	var hidden := Node3D.new()
	hidden.name = "Hidden Thing"
	hidden.visible = false
	hidden.set_meta("note", "Godot-only data that must survive a sync")
	_own(hidden, root, root)

	var packed := PackedScene.new()
	var err := packed.pack(root)
	if err == OK:
		err = ResourceSaver.save(packed, "res://scenes/reference.tscn")
	print("make_godot_ref: ", error_string(err))
	root.free()
	quit(0 if err == OK else 1)
