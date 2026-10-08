using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace TwinScene.Editor
{
    /// <summary>
    /// Warns (once per object) when you start editing something another developer has claimed, something changed in
    /// Godot that isn't synced in yet, something in conflict, or a TwinScene helper.
    /// </summary>
    [InitializeOnLoad]
    public static class TwinSceneWatcher
    {
        static readonly HashSet<GameObject> Warned = new HashSet<GameObject>();

        static TwinSceneWatcher()
        {
            Undo.postprocessModifications += OnModified;
            TwinSceneStatus.Changed += Warned.Clear;  // fresh status, fresh warnings
        }

        /// <summary>The warning for editing this object, or null. (Separate from the UI, for tests.)</summary>
        public static string WarningFor(GameObject go)
        {
            var m = TwinSceneStatus.MarkerFor(go);
            if (m.HelperPart != null) return $"\"{go.name}\" is a TwinScene helper; edit its parent instead.";
            if (m.Conflicts.Count > 0) return $"\"{go.name}\" has a conflict with Godot; settle it in Window > TwinScene first.";
            if (m.ClaimedByOther) return $"\"{go.name}\" is claimed by {m.Claim.Name}, who is working on it in Godot.";
            if (m.FromGodot != null) return $"\"{go.name}\" was changed in Godot and isn't synced in yet; editing it may cause a conflict.";
            return null;
        }

        static UndoPropertyModification[] OnModified(UndoPropertyModification[] mods)
        {
            if (!TwinSceneStatus.InRepo) return mods;
            foreach (var mod in mods)
            {
                var target = mod.currentValue?.target;
                var go = target as GameObject ?? (target as Component)?.gameObject;
                if (go == null || !Warned.Add(go)) continue;
                var warning = WarningFor(go);
                if (warning == null) continue;
                Debug.LogWarning("TwinScene: " + warning, go);
                if (SceneView.lastActiveSceneView != null)
                    SceneView.lastActiveSceneView.ShowNotification(new GUIContent(warning), 4);
            }
            return mods;
        }
    }
}
