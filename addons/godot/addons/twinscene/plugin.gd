@tool
extends EditorPlugin
## TwinScene: keeps this Godot project in sync with a Unity copy of the same game.
## Adds the TwinScene dock, colours nodes in the Scene dock, explains them in the Inspector, and warns before
## edits that would collide with work on the Unity side.

const Status := preload("res://addons/twinscene/status.gd")
const Dock := preload("res://addons/twinscene/dock.gd")
const Inspector := preload("res://addons/twinscene/inspector.gd")

var status: RefCounted
var dock: Control
var inspector: EditorInspectorPlugin
var _timer: Timer
var _seconds := 0
var _warned := {}
var _scene_tree: Tree


func _enter_tree() -> void:
	status = Status.new()
	dock = Dock.new()
	dock.plugin = self
	add_control_to_dock(DOCK_SLOT_RIGHT_BL, dock)  # its own panel, below the Inspector
	inspector = Inspector.new()
	inspector.plugin = self
	add_inspector_plugin(inspector)
	status.changed.connect(_on_status)
	scene_changed.connect(func(_root): _warned.clear(); dock.rebuild(); decorate())
	resource_saved.connect(func(_res): status.refresh())
	get_undo_redo().history_changed.connect(_on_history)
	_timer = Timer.new()
	_timer.wait_time = 1.0
	_timer.timeout.connect(_tick)
	add_child(_timer)
	_timer.start()
	Engine.set_meta("twinscene_plugin", self)
	status.refresh()


func _exit_tree() -> void:
	Engine.remove_meta("twinscene_plugin")
	remove_inspector_plugin(inspector)
	remove_control_from_docks(dock)
	dock.queue_free()
	_timer.queue_free()


func _tick() -> void:
	_seconds += 1
	if _seconds % 60 == 0:
		status.refresh()
	decorate()


func _on_status() -> void:
	_warned.clear()
	dock.rebuild()
	decorate()
	EditorInterface.get_inspector().queue_redraw()


# ------------------------------------------------------------ sync

## Save, sync both ways, and reload the open scenes so Unity's changes show up.
func sync_now(extra := PackedStringArray(), done := Callable()) -> void:
	var args := _begin_sync(extra)
	status.cli.run_async(args, func(r): _end_sync(r, done))


## The same, but wait for it (used by tests).
func sync_now_and_wait(extra := PackedStringArray()) -> Dictionary:
	var r: Dictionary = status.cli.run(_begin_sync(extra))
	_end_sync(r, Callable())
	return r


func resolve(conflict: Dictionary, side: String, done := Callable()) -> void:
	sync_now(_resolve_args(conflict, side), done)


func resolve_and_wait(conflict: Dictionary, side: String) -> Dictionary:
	return sync_now_and_wait(_resolve_args(conflict, side))


static func _resolve_args(conflict: Dictionary, side: String) -> PackedStringArray:
	return PackedStringArray(["--resolve", "%s:%s=%s" % [conflict.get("godot_key"), conflict.get("field"), side]])


func _begin_sync(extra: PackedStringArray) -> PackedStringArray:
	EditorInterface.save_all_scenes()
	status.busy = true
	dock.rebuild()
	var args := PackedStringArray(["sync", "--json"])
	args.append_array(extra)
	return args


func _end_sync(r: Dictionary, done: Callable) -> void:
	status.busy = false
	if not r["ok"]:
		status.error = str(r["error"])
		dock.rebuild()
	else:
		EditorInterface.get_resource_filesystem().scan()
		for path in EditorInterface.get_open_scenes():
			EditorInterface.reload_scene_from_path(path)
		status.refresh_now()
	if done.is_valid():
		done.call(r)


# ------------------------------------------------------------ selection

func select_node(node: Node) -> void:
	if node == null:
		return
	EditorInterface.get_selection().clear()
	EditorInterface.get_selection().add_node(node)
	EditorInterface.edit_node(node)


func select_path(path: String) -> void:
	var root := EditorInterface.get_edited_scene_root()
	if root != null:
		select_node(root.get_node_or_null(NodePath(path)))


# ------------------------------------------------------------ warnings

func _on_history() -> void:
	var root := EditorInterface.get_edited_scene_root()
	if root == null:
		return
	for node in EditorInterface.get_selection().get_selected_nodes():
		if _warned.has(node):
			continue
		_warned[node] = true
		var w: String = status.warning_for(node, root)
		if w != "":
			toast(w)


static func toast(message: String) -> void:
	push_warning("TwinScene: " + message)
	if EditorInterface.has_method("get_editor_toaster"):
		var toaster = EditorInterface.call("get_editor_toaster")
		if toaster != null:
			toaster.push_toast(message, 1)  # EditorToaster.SEVERITY_WARNING


# ------------------------------------------------------------ Scene dock colours

## Colour nodes in the Scene dock by their TwinScene state, with the reason in the tooltip.
## Godot doesn't offer a hook for this, so it finds the dock's tree; if that ever fails, nothing else breaks.
func decorate() -> int:
	if _scene_tree == null or not is_instance_valid(_scene_tree):
		_scene_tree = _find_scene_tree()
	var root := EditorInterface.get_edited_scene_root()
	if _scene_tree == null or root == null or _scene_tree.get_root() == null:
		return 0
	return _decorate_item(_scene_tree.get_root(), root)


func _decorate_item(item: TreeItem, scene_root: Node) -> int:
	# The Scene dock repaints its items (and resets colours) whenever it likes, so compare with what's actually
	# shown every time, and only touch an item when it differs (touching it makes the tree redraw).
	var count := 0
	while item != null:
		var meta = item.get_metadata(0)
		if meta is NodePath:
			var node := get_tree().root.get_node_or_null(meta) if str(meta).begins_with("/") else scene_root.get_node_or_null(meta)
			var label: Dictionary = {} if node == null else status.label_for(status.marker_for(node, scene_root))
			if not label.is_empty():
				var tip := "%s: %s" % [label["text"], label["tooltip"]]
				if item.get_custom_color(0) != label["color"]:
					item.set_custom_color(0, label["color"])
				if item.get_tooltip_text(0) != tip:
					item.set_tooltip_text(0, tip)
				item.set_meta("twinscene_label", label["text"])
				count += 1
			elif item.has_meta("twinscene_label"):
				item.clear_custom_color(0)
				item.set_tooltip_text(0, "")
				item.remove_meta("twinscene_label")
		count += _decorate_item(item.get_first_child(), scene_root)
		item = item.get_next()
	return count


## The Scene dock item for a node (tests use it to see what's really shown).
func scene_dock_item(node: Node) -> TreeItem:
	if _scene_tree == null or not is_instance_valid(_scene_tree):
		_scene_tree = _find_scene_tree()
	return null if _scene_tree == null else _find_item(_scene_tree.get_root(), node.get_path())


func _find_item(item: TreeItem, path: NodePath) -> TreeItem:
	while item != null:
		if item.get_metadata(0) == path:
			return item
		var found := _find_item(item.get_first_child(), path)
		if found != null:
			return found
		item = item.get_next()
	return null


static func _find_scene_tree() -> Tree:
	for editor in EditorInterface.get_base_control().find_children("*", "SceneTreeEditor", true, false):
		for t in editor.find_children("*", "Tree", true, false):
			return t
	return null
