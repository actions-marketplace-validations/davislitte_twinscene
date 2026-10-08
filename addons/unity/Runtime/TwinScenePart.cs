using UnityEngine;

namespace TwinScene
{
    /// <summary>
    /// Marks a helper object TwinScene made because Unity and Godot split things differently, for example a mesh
    /// with its own size. TwinScene folds it back into its parent when syncing; edit the parent instead.
    /// </summary>
    [AddComponentMenu("")]
    [DisallowMultipleComponent]
    public sealed class TwinScenePart : MonoBehaviour
    {
        [Tooltip("What this helper carries for its parent: mesh, light or camera.")]
        public string part = "mesh";
    }
}
