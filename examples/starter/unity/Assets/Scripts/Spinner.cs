using UnityEngine;

// Turns its object around an axis. Its partner in Godot is scripts/spinner.gd: TwinScene pairs the two by name and
// keeps the settings below the same in both engines (speed, axis).
public class Spinner : MonoBehaviour
{
    [Tooltip("Degrees per second")]
    public float speed = 45f;
    public Vector3 axis = Vector3.up;

    void Update()
    {
        transform.Rotate(axis, speed * Time.deltaTime, Space.Self);
    }
}
