@tool
extends RefCounted
## Runs the twinscene command line, which does all the real work; the plugin is a window onto it.
## The command is found automatically (twinscene, py -3 -m twinscene, python -m twinscene, ...), or set it in
## Editor Settings > twinscene/command, or with the TWINSCENE_COMMAND environment variable.

const CANDIDATES := ["twinscene", "py -3 -m twinscene", "python -m twinscene", "python3 -m twinscene"]
const SETTING := "twinscene/command"

## The folder holding twinscene.json above this project ("" if there isn't one).
var root := ""
var _detected := ""
var _threads: Array[Thread] = []


func _init() -> void:
	root = find_root()


static func find_root() -> String:
	var dir := ProjectSettings.globalize_path("res://").simplify_path()
	while true:
		if FileAccess.file_exists(dir.path_join("twinscene.json")):
			return dir
		var up := dir.get_base_dir()
		if up == dir or up == "":
			return ""
		dir = up
	return ""


## The command in use: the environment override, the editor setting, or the first one found to work.
func command() -> String:
	var env := OS.get_environment("TWINSCENE_COMMAND").strip_edges()
	if env != "":
		return env
	if Engine.is_editor_hint():
		var es := EditorInterface.get_editor_settings()
		if es.has_setting(SETTING) and str(es.get_setting(SETTING)).strip_edges() != "":
			return str(es.get_setting(SETTING)).strip_edges()
	if _detected != "":
		return _detected
	for c in CANDIDATES:
		if _exec(c, PackedStringArray(["--version"]))["code"] == 0:
			_detected = c
			return c
	return ""


## Run twinscene with these arguments (plus --root) and wait. Returns {ok, code, json, error, out}.
func run(args: PackedStringArray) -> Dictionary:
	if root == "":
		return _fail("This project isn't in a TwinScene repository (no twinscene.json found above it).")
	var cmd := command()
	if cmd == "":
		return _fail("Can't find the twinscene command. Install it with: pip install twinscene\n" +
			"(or set Editor Settings > twinscene/command).")
	return run_with(cmd, root, args)


## The same, in a thread; done(result) is called on the main thread.
func run_async(args: PackedStringArray, done: Callable) -> void:
	if root == "":
		done.call(run(args))
		return
	var cmd := command()
	var thread := Thread.new()
	_threads.append(thread)
	thread.start(func():
		var r := run_with(cmd, root, args)
		_finish.call_deferred(thread, done, r))


func _finish(thread: Thread, done: Callable, result: Dictionary) -> void:
	thread.wait_to_finish()
	_threads.erase(thread)
	done.call(result)


static func run_with(cmd: String, repo: String, args: PackedStringArray) -> Dictionary:
	if cmd == "":
		return _fail("Can't find the twinscene command.")
	var full := PackedStringArray(["--root", repo])
	full.append_array(args)
	var r := _exec(cmd, full)
	if r["code"] == -1:
		return _fail("Couldn't start \"%s\"." % cmd)
	r["ok"] = true
	r["error"] = ""
	r["json"] = null
	if "--json" in args:
		var lines: PackedStringArray = str(r["out"]).split("\n")
		for i in range(lines.size() - 1, -1, -1):
			if lines[i].strip_edges().begins_with("{"):
				r["json"] = JSON.parse_string(lines[i].strip_edges())
				break
		if r["json"] == null:
			return _fail("Couldn't read twinscene's output:\n" + str(r["out"]).right(600))
		if r["json"] is Dictionary and r["json"].has("error"):
			r["ok"] = false
			r["error"] = str(r["json"]["error"])
	if r["ok"] and r["code"] != 0 and r["code"] != 3:  # 3 = finished, with conflicts to settle
		r["ok"] = false
		r["error"] = str(r["out"]).strip_edges()
	return r


static func _exec(cmd: String, args: PackedStringArray) -> Dictionary:
	var parts := _split(cmd)
	if parts.is_empty():
		return {"code": -1, "out": ""}
	var all_args := parts.slice(1)
	all_args.append_array(args)
	var output: Array = []
	OS.set_environment("PYTHONIOENCODING", "utf-8")
	var code := OS.execute(parts[0], all_args, output, true)
	return {"code": code, "out": "".join(output)}


static func _split(cmd: String) -> PackedStringArray:
	var parts := PackedStringArray()
	var cur := ""
	var quoted := false
	for c in cmd:
		if c == "\"":
			quoted = not quoted
		elif (c == " " or c == "\t") and not quoted:
			if cur != "":
				parts.append(cur)
				cur = ""
		else:
			cur += c
	if cur != "":
		parts.append(cur)
	return parts


static func _fail(message: String) -> Dictionary:
	return {"ok": false, "code": -1, "json": null, "error": message, "out": ""}
