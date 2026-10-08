@tool
extends VBoxContainer
## The TwinScene dock: sync, see what's waiting, settle conflicts, and claim nodes.

var plugin: EditorPlugin
var _sync_button: Button
var _refresh_button: Button
var _state: Label
var _message: Label
var _list: VBoxContainer
var _command: LineEdit


func _ready() -> void:
	name = "TwinScene"
	var bar := HBoxContainer.new()
	add_child(bar)
	_sync_button = Button.new()
	_sync_button.text = "Sync now"
	_sync_button.tooltip_text = "Save, copy changes both ways, and reload."
	_sync_button.pressed.connect(_on_sync)
	bar.add_child(_sync_button)
	_refresh_button = Button.new()
	_refresh_button.text = "Refresh"
	_refresh_button.pressed.connect(func(): plugin.status.refresh())
	bar.add_child(_refresh_button)
	_state = Label.new()
	_state.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_state.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_state.modulate = Color(1, 1, 1, 0.6)
	bar.add_child(_state)
	_message = Label.new()
	_message.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_message.visible = false
	add_child(_message)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	add_child(scroll)
	_list = VBoxContainer.new()
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_list)
	rebuild()


func show_message(text: String) -> void:
	_message.text = text
	_message.visible = text != ""


func _on_sync() -> void:
	show_message("Syncing...")
	plugin.sync_now(PackedStringArray(), func(r): show_message(_summary(r) if r["ok"] else ""))


static func _summary(r: Dictionary) -> String:
	var to_godot := 0
	var to_unity := 0
	var conflicts := 0
	for s in r["json"].get("scenes", []):
		to_godot += s.get("to_godot", []).size()
		to_unity += s.get("to_unity", []).size()
		conflicts += s.get("conflicts", []).size()
	if to_godot + to_unity + conflicts == 0:
		return "Already in sync."
	var msg := "Synced: %d change(s) came in from Unity, %d went out to Unity." % [to_godot, to_unity]
	if conflicts > 0:
		msg += " %d conflict(s) to settle below." % conflicts
	return msg + " Commit both projects to share them."


func rebuild() -> void:
	if _list == null:
		return
	for c in _list.get_children():
		c.queue_free()
	var st = plugin.status
	_sync_button.disabled = st.busy or not st.in_repo()
	_refresh_button.disabled = st.busy or not st.in_repo()
	_state.text = "working..." if st.busy else ("checked " + st.last_checked if st.last_checked != "" else "")
	if not st.in_repo():
		_text("This project isn't part of a TwinScene repository: there's no twinscene.json in this folder or " +
			"above it. Run 'twinscene init' in the folder that holds both the Unity and the Godot project.")
		_settings()
		return
	if st.error != "":
		_text(st.error, Color(1, 0.5, 0.45))
		_settings()
		return
	var scene: Dictionary = st.edited_scene()
	if scene.is_empty():
		_text("TwinScene doesn't sync the open scene. Scenes are paired by name with the Unity project " +
			"(main.tscn <-> Main.unity), or listed in twinscene.json.")
	else:
		var incoming: Array = scene.get("changes", []).filter(func(c): return c.get("direction") == "to_godot")
		_header("Waiting to come in from Unity (%d)" % incoming.size())
		if incoming.is_empty():
			_text("Nothing: Godot has everything from Unity.", Color(1, 1, 1, 0.6))
		else:
			_text("Sync before editing these, or you may end up with conflicts.", Color(1, 1, 1, 0.6))
		for c in incoming:
			_row(str(c.get("godot_path", "")), plugin.status.describe(c))
		var conflicts: Array = scene.get("conflicts", [])
		if not conflicts.is_empty():
			_header("Conflicts (%d)" % conflicts.size())
			_text("Changed differently in Unity and in Godot. Nothing was overwritten: pick the version to keep.",
				Color(1, 0.75, 0.4))
			for c in conflicts:
				_conflict(c)
		var outgoing: Array = scene.get("changes", []).filter(func(c): return c.get("direction") == "to_unity")
		if not outgoing.is_empty():
			_header("Your changes going to Unity on the next sync (%d)" % outgoing.size())
			for c in outgoing:
				_row(str(c.get("godot_path", "")), plugin.status.describe(c))
	_claims(scene)
	_settings()


func _claims(scene: Dictionary) -> void:
	var st = plugin.status
	var shown: Array = st.claims.filter(func(c): return scene.is_empty() or c.get("scene") == scene.get("scene"))
	_header("Claims (%d)" % shown.size())
	if st.claims_error != "":
		_text("Claims need the repository's git remote: " + st.claims_error, Color(1, 1, 1, 0.6))
	for c in shown:
		var mine: bool = str(c.get("who")) == st.me
		_row(str(c.get("godot_path", "")), "you" if mine else st.who_name(c))
	var bar := HBoxContainer.new()
	_list.add_child(bar)
	for spec in [["Claim selected", false], ["Release selected", true]]:
		var b := Button.new()
		b.text = spec[0]
		var release: bool = spec[1]
		b.pressed.connect(func(): _claim_selected(release))
		bar.add_child(b)
	var all := Button.new()
	all.text = "Release all mine"
	all.pressed.connect(func(): _report(plugin.status.release_all()))
	bar.add_child(all)


func _claim_selected(release: bool) -> void:
	var root := EditorInterface.get_edited_scene_root()
	var nodes := EditorInterface.get_selection().get_selected_nodes()
	if root == null or nodes.is_empty():
		show_message("Select a node first.")
		return
	_report(plugin.status.claim(nodes[0], root, release))


func _report(r: Dictionary) -> void:
	show_message("" if r["ok"] else str(r["error"]))


func _conflict(c: Dictionary) -> void:
	var box := PanelContainer.new()
	_list.add_child(box)
	var v := VBoxContainer.new()
	box.add_child(v)
	var head := HBoxContainer.new()
	v.add_child(head)
	head.add_child(_link(str(c.get("godot_path", ""))))
	var f := Label.new()
	f.text = str(c.get("field", ""))
	f.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	f.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	head.add_child(f)
	for side in [["Unity", "unity", "Use Unity's"], ["Godot", "godot", "Keep Godot's"]]:
		var row := HBoxContainer.new()
		v.add_child(row)
		var l := Label.new()
		l.text = "%s: %s" % [side[0], show_value(c.get(side[1]))]
		l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		row.add_child(l)
		var b := Button.new()
		b.text = side[2]
		b.disabled = plugin.status.busy
		var which: String = side[1]
		b.pressed.connect(func(): plugin.resolve(c, which))
		row.add_child(b)


static func show_value(v) -> String:
	if v == null:
		return "(none)"
	if v is Array:
		var parts := PackedStringArray()
		for x in v:
			parts.append(show_value(x))
		return "(" + ", ".join(parts) + ")"
	if v is float:
		return str(snappedf(v, 0.001))
	if v is bool:
		return "on" if v else "off"
	return str(v)


func _row(path: String, detail: String) -> void:
	var row := HBoxContainer.new()
	_list.add_child(row)
	row.add_child(_link(path))
	var l := Label.new()
	l.text = detail
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	l.modulate = Color(1, 1, 1, 0.7)
	row.add_child(l)


func _link(path: String) -> LinkButton:
	var b := LinkButton.new()
	b.text = path
	b.underline = LinkButton.UNDERLINE_MODE_ON_HOVER
	b.pressed.connect(func(): plugin.select_path(path))
	return b


func _header(text: String) -> void:
	var l := Label.new()
	l.text = text
	l.add_theme_font_override("font", get_theme_font("bold", "EditorFonts"))
	_list.add_child(HSeparator.new())
	_list.add_child(l)


func _text(text: String, color := Color(1, 1, 1)) -> void:
	var l := Label.new()
	l.text = text
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.modulate = color
	_list.add_child(l)


func _settings() -> void:
	_header("Settings")
	_text("twinscene command (empty: find it automatically)", Color(1, 1, 1, 0.6))
	var row := HBoxContainer.new()
	_list.add_child(row)
	_command = LineEdit.new()
	_command.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var es := EditorInterface.get_editor_settings()
	_command.text = str(es.get_setting(plugin.status.cli.SETTING)) if es.has_setting(plugin.status.cli.SETTING) else ""
	row.add_child(_command)
	var save := Button.new()
	save.text = "Save"
	save.pressed.connect(func():
		es.set_setting(plugin.status.cli.SETTING, _command.text.strip_edges())
		plugin.status.cli._detected = ""
		plugin.status.refresh())
	row.add_child(save)
	_text("Repository: " + (plugin.status.cli.root if plugin.status.cli.root != "" else "(none)"), Color(1, 1, 1, 0.6))
