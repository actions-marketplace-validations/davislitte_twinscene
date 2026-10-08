@tool
extends RefCounted
## What twinscene says about this repository right now: changes waiting on either side, conflicts and claims.
## Nodes are matched by their path in the scene (Godot doesn't expose node ids to scripts).

signal changed

const Cli := preload("res://addons/twinscene/cli.gd")

## Node types whose settings TwinScene copies to Unity.
const SYNCED_TYPES := {
	"Node3D": "position, rotation, scale, visibility",
	"MeshInstance3D": "mesh, materials", "Camera3D": "camera", "DirectionalLight3D": "light",
	"OmniLight3D": "light", "SpotLight3D": "light", "CollisionShape3D": "collision shape",
	"StaticBody3D": "body", "RigidBody3D": "body, mass", "CharacterBody3D": "body", "AnimatableBody3D": "body",
}

var cli := Cli.new()
var scenes: Array = []   # dictionaries from `twinscene status --json`
var claims: Array = []
var me := ""
var error := ""
var claims_error := ""
var busy := false
var version := 0         # goes up every time the status changes
var last_checked := ""


func in_repo() -> bool:
	return cli.root != ""


# ------------------------------------------------------------ refresh

## Ask twinscene for the current status in the background (with claims, which needs the network).
func refresh() -> void:
	if busy or not in_repo():
		return
	busy = true
	cli.run_async(PackedStringArray(["status", "--json", "--claims"]), func(r):
		busy = false
		apply(r))


## The same, but wait for it.
func refresh_now() -> Dictionary:
	var r := cli.run(PackedStringArray(["status", "--json", "--claims"]))
	apply(r)
	return r


func apply(r: Dictionary) -> void:
	last_checked = Time.get_time_string_from_system()
	error = "" if r["ok"] else str(r["error"])
	var j = r["json"]
	if r["ok"] and j is Dictionary:
		scenes = j.get("scenes", [])
		me = str(j.get("me", ""))
		claims_error = str(j.get("claims_error", ""))
		var cl: Array = j.get("claims", [])
		if not cl.is_empty() or claims_error == "":
			claims = cl
	version += 1
	changed.emit()


func apply_claims(r: Dictionary) -> void:
	if r["ok"] and r["json"] is Dictionary:
		me = str(r["json"].get("me", ""))
		claims = r["json"].get("claims", [])
		version += 1
		changed.emit()


# ------------------------------------------------------------ lookups

## The TwinScene scene for a Godot scene file (empty if TwinScene doesn't sync it).
func scene_for(scene_file: String) -> Dictionary:
	for s in scenes:
		if str(s.get("godot_scene", "")) == scene_file:
			return s
	return {}


## The edited scene's TwinScene scene, and the path TwinScene uses for a node in it.
func edited_scene() -> Dictionary:
	var root := EditorInterface.get_edited_scene_root()
	return {} if root == null else scene_for(root.scene_file_path)


static func path_of(node: Node, scene_root: Node) -> String:
	return str(scene_root.get_path_to(node))


## Everything TwinScene knows about one node:
## {from_unity: change, to_unity: change, conflicts: [..], claim: {..}, claimed_by_other: bool, helper: part}
func marker_for(node: Node, scene_root: Node) -> Dictionary:
	var m := {"from_unity": {}, "to_unity": {}, "conflicts": [], "claim": {}, "claimed_by_other": false, "helper": ""}
	if node.has_meta("twinscene_part"):
		m["helper"] = str(node.get_meta("twinscene_part"))
	if scene_root == null or node == scene_root:
		return m
	var s := scene_for(scene_root.scene_file_path)
	if s.is_empty():
		return m
	var path := path_of(node, scene_root)
	for c in s.get("changes", []):
		if str(c.get("godot_path", "")) == path:
			if c.get("direction") == "to_godot":
				m["from_unity"] = c
			else:
				m["to_unity"] = c
	for c in s.get("conflicts", []):
		if str(c.get("godot_path", "")) == path:
			m["conflicts"].append(c)
	for c in claims:
		if str(c.get("scene", "")) == str(s.get("scene", "")) and str(c.get("godot_path", "")) == path:
			m["claim"] = c
			m["claimed_by_other"] = str(c.get("who", "")) != me
	return m


static func who_name(claim: Dictionary) -> String:
	var who := str(claim.get("who", ""))
	return who.get_slice("<", 0).strip_edges() if "<" in who else who


static func describe(change: Dictionary) -> String:
	if change.get("kind") == "changed":
		return ", ".join(PackedStringArray(change.get("fields", [])))
	return str(change.get("kind", ""))


## Short label, colour and tooltip for the Scene dock, or {} for none.
func label_for(m: Dictionary) -> Dictionary:
	if not m["conflicts"].is_empty():
		var fields := PackedStringArray()
		for c in m["conflicts"]:
			fields.append(str(c.get("field", "")))
		return {"text": "conflict", "color": Color(0.95, 0.35, 0.3),
			"tooltip": "Changed differently in Unity and Godot: %s. Settle it in the TwinScene dock." % ", ".join(fields)}
	if m["claimed_by_other"]:
		return {"text": "claimed: " + who_name(m["claim"]), "color": Color(0.95, 0.65, 0.2),
			"tooltip": "%s is working on this (since %s)." % [m["claim"].get("who"), m["claim"].get("when")]}
	if not m["from_unity"].is_empty():
		var deleted: bool = m["from_unity"].get("kind") == "deleted"
		return {"text": "deleted in Unity" if deleted else "Unity changed", "color": Color(0.4, 0.7, 1.0),
			"tooltip": "Changed in Unity, not synced in yet: %s. Sync before editing." % describe(m["from_unity"])}
	if not m["claim"].is_empty():
		return {"text": "claimed: you", "color": Color(0.55, 0.8, 0.55), "tooltip": "You claimed this."}
	if m["helper"] != "":
		return {"text": "helper", "color": Color(0.6, 0.6, 0.6),
			"tooltip": "TwinScene helper carrying the %s for its parent. Edit the parent instead." % m["helper"]}
	return {}


## The warning for editing this node, or "".
func warning_for(node: Node, scene_root: Node) -> String:
	var m := marker_for(node, scene_root)
	if m["helper"] != "":
		return "\"%s\" is a TwinScene helper; edit its parent instead." % node.name
	if not m["conflicts"].is_empty():
		return "\"%s\" has a conflict with Unity; settle it in the TwinScene dock first." % node.name
	if m["claimed_by_other"]:
		return "\"%s\" is claimed by %s, who is working on it in Unity." % [node.name, who_name(m["claim"])]
	if not m["from_unity"].is_empty():
		return "\"%s\" was changed in Unity and isn't synced in yet; editing it may cause a conflict." % node.name
	return ""


## [what syncs with Unity, what stays in Godot] for a node.
static func coverage(node: Node) -> PackedStringArray:
	var synced := PackedStringArray()
	var cls := node.get_class()
	if node is Node3D:
		synced.append(SYNCED_TYPES["Node3D"])
	if SYNCED_TYPES.has(cls) and cls != "Node3D":
		synced.append(SYNCED_TYPES[cls])
	var only := PackedStringArray()
	if not SYNCED_TYPES.has(cls):
		only.append(cls)
	if node.get_script() != null:
		only.append("script " + str(node.get_script().resource_path.get_file()))
	return PackedStringArray([", ".join(synced), ", ".join(only)])


# ------------------------------------------------------------ actions

## Claim (or release) a node for the current user.
func claim(node: Node, scene_root: Node, release := false) -> Dictionary:
	var s := scene_for(scene_root.scene_file_path)
	if s.is_empty():
		return {"ok": false, "error": "Save and sync the scene first, so both sides know this node."}
	var r := cli.run(PackedStringArray(["release" if release else "claim", "--scene", str(s["scene"]),
		"--godot-path", path_of(node, scene_root), "--json"]))
	apply_claims(r)
	return r


func release_all() -> Dictionary:
	var r := cli.run(PackedStringArray(["release", "--all", "--json"]))
	apply_claims(r)
	return r
