extends Node3D
## Turns its node around an axis. Its partner in Unity is Assets/Scripts/Spinner.cs: TwinScene pairs the two by name
## and keeps the settings below the same in both engines (speed, axis).

@export var speed := 45.0  ## degrees per second
@export var axis := Vector3.UP


func _process(delta: float) -> void:
	# Unity's axes are a mirror image of Godot's, so the same angle turns the other way round there. The minus keeps
	# both engines turning the same way on screen.
	rotate_object_local(axis.normalized(), -deg_to_rad(speed) * delta)
