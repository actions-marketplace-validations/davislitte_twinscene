using UnityEditor;
using UnityEngine;

namespace TwinScene.Editor
{
    /// <summary>The TwinScenePart component explains itself instead of showing a bare text field.</summary>
    [CustomEditor(typeof(TwinScenePart))]
    public class TwinScenePartEditor : UnityEditor.Editor
    {
        public override void OnInspectorGUI()
        {
            var part = (TwinScenePart)target;
            var parent = part.transform.parent;
            EditorGUILayout.HelpBox(
                $"Made by TwinScene to carry the {part.part} for \"{(parent ? parent.name : "?")}\". Godot stores " +
                "it separately, so Unity needs a helper object too. Edit the parent instead, and don't delete this " +
                "while the parent uses it.", MessageType.Info);
            if (parent != null && GUILayout.Button("Select the parent"))
                Selection.activeGameObject = parent.gameObject;
        }
    }
}
