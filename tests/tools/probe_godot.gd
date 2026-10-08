# Loads a scene in the real Godot and writes where everything ends up, for compare_probes.py.
# Run: godot --headless --path <project> --script probe_godot.gd -- <res://scene.tscn> <out.json>
extends SceneTree

const ASPECT := Vector2i(1600, 900)


func _init() -> void:
	var args := OS.get_cmdline_user_args()
	var packed: PackedScene = load(args[0])
	if packed == null:
		push_error("probe: can't load " + args[0])
		quit(1)
		return
	var root := packed.instantiate()
	# A fixed-size viewport, so screen positions don't depend on the (headless) window.
	var vp := SubViewport.new()
	vp.size = ASPECT
	get_root().add_child(vp)
	# Compare the scene as saved, like Unity does: the game's own scripts mustn't add, move or hide things.
	for n in [root] + root.find_children("*", "", true, false):
		n.set_script(null)
	root.process_mode = Node.PROCESS_MODE_DISABLED
	vp.add_child(root)
	await process_frame
	var cam: Camera3D = null
	for n in root.find_children("*", "Camera3D", true, false):
		cam = n
		break
	if cam:
		cam.current = true
	var out := {}
	for n in [root] + root.find_children("*", "", true, false):
		if n == root or n is CanvasItem or n is CanvasLayer:
			continue
		if n.owner == null:  # made by a script at start-up, not saved in the scene
			continue
		var helper := false  # TwinScene helpers, and anything inside them or inside an imported model file
		var anc: Node = n.get_parent()
		if n.has_meta("twinscene_part"):
			helper = true
		while anc != null and anc != root and not helper:
			if anc.has_meta("twinscene_part") or (anc.scene_file_path != "" and not anc.scene_file_path.ends_with(".tscn")):
				helper = true
			anc = anc.get_parent()
		if helper or n is Control:
			continue
		var path := str(root.get_path_to(n))
		var at: Node = n  # a non-3D node (Timer, WorldEnvironment...) sits where its nearest 3D parent is
		while at != null and not (at is Node3D):
			at = at.get_parent()
		var g: Transform3D = at.global_transform if at != null else Transform3D()
		var e := {"type": n.get_class(), "pos": [g.origin.x, g.origin.y, g.origin.z],
			"active": at.is_visible_in_tree() if at != null else true}
		# where the node "looks": -Z for cameras, directional and spot lights; +Z for the rest (Godot's model front)
		if n is Light3D:
			var rng := 0.0
			if n is OmniLight3D:
				rng = n.omni_range
			elif n is SpotLight3D:
				rng = n.spot_range
			e["light"] = [n.light_color.r, n.light_color.g, n.light_color.b, n.light_energy, rng]
		var turned: bool = n is Camera3D or n is DirectionalLight3D or n is SpotLight3D
		var look := (-g.basis.z if turned else g.basis.z).normalized()
		var up := g.basis.y.normalized()
		e["look"] = [look.x, look.y, look.z]
		e["up"] = [up.x, up.y, up.z]
		if cam and at != null and not cam.is_position_behind(g.origin):
			var px := cam.unproject_position(g.origin)
			e["screen"] = [px.x / ASPECT.x, 1.0 - px.y / ASPECT.y]
		var model_root: bool = n.scene_file_path != "" and not n.scene_file_path.ends_with(".tscn")
		var boxes: Array = []  # all the meshes of the node (its own, its mesh helper's, or a placed model's)
		if n is MeshInstance3D and n.mesh:
			boxes.append(n.global_transform * n.get_aabb())
		for k in n.get_children():
			if k.has_meta("twinscene_part") and str(k.get_meta("twinscene_part")) == "mesh":
				for m in [k] + k.find_children("*", "MeshInstance3D", true, false):
					if m is MeshInstance3D and m.mesh:
						boxes.append(m.global_transform * m.get_aabb())
		if boxes.is_empty() and model_root:
			for m in n.find_children("*", "MeshInstance3D", true, false):
				if m.mesh:
					boxes.append(m.global_transform * m.get_aabb())
		if not boxes.is_empty():
			var box: AABB = boxes[0]
			for b in boxes:
				box = box.merge(b)
			e["bounds"] = [box.position.x, box.position.y, box.position.z, box.end.x, box.end.y, box.end.z]
		out[path] = e
	var f := FileAccess.open(args[1], FileAccess.WRITE)
	f.store_string(JSON.stringify(out, " "))
	f.close()
	print("probe: wrote ", out.size(), " objects")
	quit(0)


func _first_mesh(n: Node) -> MeshInstance3D:
	for k in n.find_children("*", "MeshInstance3D", true, false):
		return k
	return null
