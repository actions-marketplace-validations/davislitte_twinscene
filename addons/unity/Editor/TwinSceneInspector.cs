using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace TwinScene.Editor
{
    /// <summary>At the top of the Inspector: the object's TwinScene state and which components sync with Godot.</summary>
    [InitializeOnLoad]
    public static class TwinSceneInspector
    {
        /// <summary>Components TwinScene copies to Godot (everything else stays in Unity).</summary>
        static readonly HashSet<System.Type> Synced = new HashSet<System.Type>
        {
            typeof(Transform), typeof(MeshFilter), typeof(MeshRenderer), typeof(Camera), typeof(Light),
            typeof(BoxCollider), typeof(SphereCollider), typeof(CapsuleCollider), typeof(Rigidbody), typeof(TwinScenePart),
        };

        static TwinSceneInspector()
        {
            UnityEditor.Editor.finishedDefaultHeaderGUI += OnHeader;
        }

        /// <summary>"Synced with Godot: ..." / "Unity only: ..." for a GameObject. (Separate from the drawing, for tests.)</summary>
        public static (string synced, string unityOnly) Coverage(GameObject go)
        {
            var comps = go.GetComponents<Component>().Where(c => c != null).ToList();
            var synced = comps.Where(c => Synced.Contains(c.GetType())).Select(Name).Distinct();
            var other = comps.Where(c => !Synced.Contains(c.GetType())).Select(Name).Distinct();
            if (go.GetComponents<Component>().Any(c => c == null)) other = other.Append("missing script");
            return (string.Join(", ", synced), string.Join(", ", other));
        }

        static string Name(Component c) => c is MonoBehaviour ? c.GetType().Name + " (script)" : ObjectNames.NicifyVariableName(c.GetType().Name);

        static void OnHeader(UnityEditor.Editor editor)
        {
            if (!(editor.target is GameObject go) || editor.targets.Length != 1 || !TwinSceneStatus.InRepo) return;
            if (TwinSceneStatus.For(go.scene) == null) return;
            var m = TwinSceneStatus.MarkerFor(go);
            if (m.HelperPart != null)
            {
                EditorGUILayout.HelpBox($"TwinScene helper: it carries the {m.HelperPart} for \"{go.transform.parent?.name}\", " +
                                        "because Godot keeps it separately. Edit the parent instead; changes here " +
                                        "aren't synced.", MessageType.Info);
                return;
            }
            foreach (var c in m.Conflicts)
                using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
                {
                    EditorGUILayout.LabelField($"Conflict on {c.Field}: Unity {MiniJson.Show(c.UnityValue)}, Godot {MiniJson.Show(c.GodotValue)}",
                                               EditorStyles.wordWrappedLabel);
                    using (new EditorGUILayout.HorizontalScope())
                    using (new EditorGUI.DisabledScope(TwinSceneStatus.Busy))
                    {
                        if (GUILayout.Button("Keep Unity's")) TwinSceneStatus.Resolve(c, "unity");
                        if (GUILayout.Button("Use Godot's")) TwinSceneStatus.Resolve(c, "godot");
                    }
                }
            if (m.ClaimedByOther)
                EditorGUILayout.HelpBox($"{m.Claim.Who} is working on this in Godot. Check with them before editing.", MessageType.Warning);
            if (m.FromGodot != null)
                EditorGUILayout.HelpBox(m.FromGodot.Kind == "deleted"
                    ? "This was deleted in Godot. Sync to remove it here too (or keep editing to keep it)."
                    : $"Changed in Godot ({m.FromGodot.Describe()}), not synced in yet. Sync before editing it.",
                    MessageType.Info);
            var (synced, only) = Coverage(go);
            var text = "Synced with Godot: " + (synced == "" ? "nothing" : synced);
            if (only != "") text += "\nUnity only: " + only;
            EditorGUILayout.LabelField(text, EditorStyles.wordWrappedMiniLabel);
        }
    }
}
