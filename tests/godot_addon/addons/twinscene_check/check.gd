@tool
extends EditorPlugin
## Exercises the TwinScene plugin inside the real editor (run by tests/tools/check_godot_addon.py, which prepares a
## repository where Unity moved Box/Child and brightened Sun, both sides moved Lamp, and "Bo" claimed Ball).
## Prints "CHECK PASS/FAIL ..." lines and quits.

var fails := 0


func _enter_tree() -> void:
	if OS.get_environment("TWINSCENE_CHECK") == "1":
		_run.call_deferred()


func check(ok: bool, what: String) -> void:
	print(("CHECK PASS " if ok else "CHECK FAIL ") + what)
	if not ok:
		fails += 1


func _frames(n: int) -> void:
	for i in n:
		await get_tree().process_frame


func _labels(node: Node) -> String:
	var out := PackedStringArray()
	for l in node.find_children("*", "Label", true, false):
		out.append(l.text)
	return "\n".join(out)


func _run() -> void:
	await get_tree().create_timer(1.0).timeout
	var p = Engine.get_meta("twinscene_plugin") if Engine.has_meta("twinscene_plugin") else null
	check(p != null, "the TwinScene plugin is loaded")
	if p == null:
		_done()
		return
	check(p.dock != null and p.dock.is_inside_tree(), "the TwinScene dock is in the editor")
	EditorInterface.open_scene_from_path("res://scenes/main.tscn")
	await _frames(5)
	var root := EditorInterface.get_edited_scene_root()
	check(root != null and root.scene_file_path == "res://scenes/main.tscn", "the synced scene is open")
	var r: Dictionary = p.status.refresh_now()
	check(r["ok"], "status runs through the twinscene command " + str(r["error"]))
	check(str(p.status.me).begins_with("Ana"), "knows who I am: " + str(p.status.me))
	var st = p.status

	# -- waiting changes from Unity
	var child := root.get_node("Box/Child")
	var m: Dictionary = st.marker_for(child, root)
	check(not m["from_unity"].is_empty() and "position" in m["from_unity"].get("fields", []), "Box/Child: Unity's move is waiting")
	check(st.label_for(m).get("text") == "Unity changed", "Box/Child: Scene dock label 'Unity changed'")
	check("changed in Unity" in st.warning_for(child, root), "Box/Child: editing it warns")
	var sun_m: Dictionary = st.marker_for(root.get_node("Sun"), root)
	check("light.energy" in sun_m["from_unity"].get("fields", []), "Sun: Unity's brighter light is waiting")

	# -- conflict
	var lamp := root.get_node("Lamp")
	var lm: Dictionary = st.marker_for(lamp, root)
	check(lm["conflicts"].size() == 1 and lm["conflicts"][0].get("field") == "position", "Lamp: position conflict found")
	check(st.label_for(lm).get("text") == "conflict", "Lamp: Scene dock label 'conflict'")

	# -- claims
	var ball := root.get_node("Ball")
	var bm: Dictionary = st.marker_for(ball, root)
	check(bm["claimed_by_other"] and st.who_name(bm["claim"]) == "Bo", "Ball: claimed by Bo")
	check(st.label_for(bm).get("text") == "claimed: Bo", "Ball: Scene dock label 'claimed: Bo'")
	check("claimed by Bo" in st.warning_for(ball, root), "Ball: editing it warns")
	var taken: Dictionary = st.claim(ball, root)
	check(not taken["ok"] and "already claimed" in str(taken["error"]), "Ball: can't claim Bo's node")
	var mine: Dictionary = st.claim(root.get_node("Sun"), root)
	var sun_after: Dictionary = st.marker_for(root.get_node("Sun"), root)
	check(mine["ok"] and str(sun_after["claim"].get("who")) == st.me and not sun_after["claimed_by_other"],
		"Sun: I can claim it " + str(mine.get("error", "")))
	# (Sun's label stays "Unity changed": a waiting change matters more than my own claim)
	check(st.label_for(sun_after).get("text") == "Unity changed", "Sun: waiting change outranks my claim in the label")

	# -- helpers TwinScene made (Unity's ball has its collider on the same object)
	var helper := root.get_node_or_null("Ball/Ball (collider)")
	check(helper != null, "Ball: has its collider helper")
	if helper != null:
		var hm: Dictionary = st.marker_for(helper, root)
		check(hm["helper"] == "collider" and st.label_for(hm).get("text") == "helper", "Ball helper: Scene dock label 'helper'")
		check("helper" in st.warning_for(helper, root), "Ball helper: editing it warns")
		check("TwinScene helper" in _labels(p.inspector.panel_for(helper, root)), "Ball helper: inspector explains it")

	# -- inspector
	check(p.inspector._can_handle(lamp), "inspector panel shows for synced nodes")
	var panel: Control = p.inspector.panel_for(lamp, root)
	check("Conflict on position" in _labels(panel) and panel.find_children("*", "Button", true, false).size() == 2,
		"Lamp: inspector shows the conflict with two buttons")
	check("body" in st.coverage(ball)[0], "Ball: inspector lists what syncs")
	panel.free()

	# -- dock
	p.dock.rebuild()
	await _frames(2)
	var dock_text := _labels(p.dock)
	check("Waiting to come in from Unity (2)" in dock_text, "dock lists the 2 waiting changes")
	check("Conflicts (1)" in dock_text, "dock lists the conflict")

	# -- Scene dock colours: what's really shown, after the dock has had time to repaint itself
	p.select_node(lamp)
	await get_tree().create_timer(2.5).timeout
	var coloured: int = p.decorate()
	check(coloured >= 4, "Scene dock: %d nodes labelled (Box/Child, Sun, Lamp, Ball, helpers)" % coloured)
	for spec in [["Box/Child", "Unity changed"], ["Lamp", "conflict"], ["Ball", "claimed: Bo"]]:
		var node := root.get_node(spec[0])
		var item: TreeItem = p.scene_dock_item(node)
		var want: Color = st.label_for(st.marker_for(node, root))["color"]
		check(item != null and item.get_custom_color(0) == want and str(spec[1]) in item.get_tooltip_text(0),
			"Scene dock: %s is shown coloured as '%s'" % spec)

	var shot := OS.get_environment("TWINSCENE_SCREENSHOT")
	if shot != "":  # (only in a windowed run) the editor as a person would see it, with Lamp selected
		p.select_node(lamp)
		p.dock.rebuild()
		await get_tree().create_timer(3.0).timeout
		p.decorate()
		await _frames(10)
		EditorInterface.get_base_control().get_viewport().get_texture().get_image().save_png(shot)
		print("screenshot: ", shot)

	# -- settle the conflict with Unity's value (this also syncs everything else and reloads the scene)
	var res: Dictionary = p.resolve_and_wait(lm["conflicts"][0], "unity")
	check(res["ok"], "resolve runs " + str(res["error"]))
	await _frames(5)
	root = EditorInterface.get_edited_scene_root()
	var lamp_pos: Vector3 = root.get_node("Lamp").position
	check(lamp_pos.is_equal_approx(Vector3(-5, 0, 0)), "Lamp: took Unity's position %s" % lamp_pos)
	var child_pos: Vector3 = root.get_node("Box/Child").position
	check(child_pos.is_equal_approx(Vector3(0, 3, 0)), "Box/Child: Unity's move came in %s" % child_pos)
	check(is_equal_approx(root.get_node("Sun").light_energy, 3.0), "Sun: Unity's brightness came in")
	var scene: Dictionary = p.status.edited_scene()
	check(scene.get("changes", []).is_empty() and scene.get("conflicts", []).is_empty(), "nothing left to sync")

	var rel: Dictionary = p.status.release_all()
	check(rel["ok"] and p.status.claims.all(func(c): return str(c.get("who")) != p.status.me), "release all my claims")
	check(p.status.claims.any(func(c): return p.status.who_name(c) == "Bo"), "Bo's claim is still there")
	_done()


func _done() -> void:
	print("CHECK DONE %d failed" % fails)
	get_tree().quit(0 if fails == 0 else 1)
