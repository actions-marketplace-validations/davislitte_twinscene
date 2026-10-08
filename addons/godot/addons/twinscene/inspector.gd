@tool
extends EditorInspectorPlugin
## At the top of the Inspector: the node's TwinScene state and what of it syncs with Unity.

var plugin: EditorPlugin


func _can_handle(object: Object) -> bool:
	if not (object is Node) or not plugin.status.in_repo():
		return false
	var root := EditorInterface.get_edited_scene_root()
	return root != null and (object == root or root.is_ancestor_of(object)) and \
		not plugin.status.scene_for(root.scene_file_path).is_empty()


func _parse_begin(object: Object) -> void:
	add_custom_control(panel_for(object as Node, EditorInterface.get_edited_scene_root()))


## The panel for a node (built separately from the inspector hook, for tests).
func panel_for(node: Node, scene_root: Node) -> VBoxContainer:
	var st = plugin.status
	var box := VBoxContainer.new()
	box.name = "TwinScenePanel"
	var m: Dictionary = st.marker_for(node, scene_root)
	if m["helper"].begins_with("script:"):
		_note(box, "TwinScene helper: it holds the %s script for \"%s\" (in Unity that script is on the object " % [
			m["helper"].substr(7), node.get_parent().name] + "itself, but this node's type can't hold it). Its " +
			"settings sync; move or rename the parent instead of this node.", Color(0.75, 0.75, 0.75))
	elif m["helper"] != "":
		_note(box, "TwinScene helper: it carries the %s for \"%s\", because Unity keeps it on the object itself. " % [
			m["helper"], node.get_parent().name] + "Edit the parent instead; changes here aren't synced.",
			Color(0.75, 0.75, 0.75))
	if m["helper"] != "":
		var b := Button.new()
		b.text = "Select the parent"
		b.pressed.connect(func(): plugin.select_node(node.get_parent()))
		box.add_child(b)
		return box
	for c in m["conflicts"]:
		_note(box, "Conflict on %s: Unity %s, Godot %s" % [c.get("field"), _show(c.get("unity")), _show(c.get("godot"))],
			Color(1, 0.55, 0.5))
		var row := HBoxContainer.new()
		box.add_child(row)
		for spec in [["Use Unity's", "unity"], ["Keep Godot's", "godot"]]:
			var b := Button.new()
			b.text = spec[0]
			var side: String = spec[1]
			b.pressed.connect(func(): plugin.resolve(c, side))
			row.add_child(b)
	if m["claimed_by_other"]:
		_note(box, "%s is working on this in Unity. Check with them before editing." % m["claim"].get("who"),
			Color(1, 0.75, 0.4))
	if not m["from_unity"].is_empty():
		if m["from_unity"].get("kind") == "deleted":
			_note(box, "This was deleted in Unity. Sync to remove it here too (or keep editing to keep it).",
				Color(0.55, 0.8, 1))
		else:
			_note(box, "Changed in Unity (%s), not synced in yet. Sync before editing it." % st.describe(m["from_unity"]),
				Color(0.55, 0.8, 1))
	if node != scene_root:
		var cov: PackedStringArray = st.coverage(node)
		var text := "Synced with Unity: " + (cov[0] if cov[0] != "" else "nothing")
		if cov[1] != "":
			text += "\nGodot only: " + cov[1]
		_note(box, text, Color(1, 1, 1, 0.6))
	return box


static func _show(v) -> String:
	return preload("res://addons/twinscene/dock.gd").show_value(v)


static func _note(box: VBoxContainer, text: String, color: Color) -> void:
	var l := Label.new()
	l.text = text
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.custom_minimum_size.x = 150
	l.modulate = color
	box.add_child(l)
