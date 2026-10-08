using System;
using System.Linq;
using System.Linq.Expressions;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace TwinScene.Editor
{
    /// <summary>Small labels in the Hierarchy: "Godot changed", "conflict", "claimed: Ana", "helper".</summary>
    [InitializeOnLoad]
    public static class TwinSceneHierarchy
    {
        static GUIStyle _style;
        static MethodInfo _toObject;

        /// <summary>Whether the labels are hooked into this Unity's Hierarchy window.</summary>
        public static bool Hooked => _toObject != null;

        static TwinSceneHierarchy()
        {
            Hook();
        }

        /// <summary>
        /// Unity 6.6 removed hierarchyWindowItemOnGUI (instance ids) in favour of hierarchyWindowItemByEntityIdOnGUI,
        /// which older versions don't have. Hook whichever this Unity has, without naming either at compile time.
        /// </summary>
        static void Hook()
        {
            const BindingFlags flags = BindingFlags.Public | BindingFlags.Static;
            var app = typeof(EditorApplication);
            var field = app.GetField("hierarchyWindowItemByEntityIdOnGUI", flags) ?? app.GetField("hierarchyWindowItemOnGUI", flags);
            if (field == null) return;
            var idType = field.FieldType.GetMethod("Invoke").GetParameters()[0].ParameterType;
            _toObject = typeof(EditorUtility).GetMethods(flags).FirstOrDefault(m =>
                (m.Name == "EntityIdToObject" || m.Name == "InstanceIDToObject") &&
                m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType == idType);
            if (_toObject == null) return;
            var id = Expression.Parameter(idType, "id");
            var rect = Expression.Parameter(typeof(Rect), "rect");
            var body = Expression.Call(typeof(TwinSceneHierarchy).GetMethod(nameof(OnItem), BindingFlags.NonPublic | BindingFlags.Static),
                                       Expression.Convert(id, typeof(object)), rect);
            var callback = Expression.Lambda(field.FieldType, body, id, rect).Compile();
            field.SetValue(null, Delegate.Combine((Delegate)field.GetValue(null), callback));
        }

        /// <summary>The label and colour for an object, or null for none. (Separate from the drawing, for tests.)</summary>
        public static (string text, Color color, string tooltip)? Label(TwinSceneStatus.Marker m)
        {
            if (m.Conflicts.Count > 0)
                return ("conflict", new Color(0.95f, 0.35f, 0.3f),
                        "Changed differently in Unity and Godot: " + string.Join(", ", m.Conflicts.ConvertAll(c => c.Field)) +
                        ". Settle it in Window > TwinScene.");
            if (m.ClaimedByOther)
                return ("claimed: " + m.Claim.Name, new Color(0.95f, 0.65f, 0.2f),
                        $"{m.Claim.Who} is working on this (since {m.Claim.When}).");
            if (m.FromGodot != null)
                return (m.FromGodot.Kind == "deleted" ? "deleted in Godot" : "Godot changed", new Color(0.4f, 0.7f, 1f),
                        "Changed in Godot, not synced in yet: " + m.FromGodot.Describe() + ". Sync before editing.");
            if (m.Claim != null)
                return ("claimed: you", new Color(0.55f, 0.8f, 0.55f), "You claimed this.");
            if (m.HelperPart != null)
                return ("helper", new Color(0.6f, 0.6f, 0.6f),
                        $"TwinScene helper carrying the {m.HelperPart} for its parent. Edit the parent instead.");
            return null;
        }

        static void OnItem(object id, Rect rect)
        {
            if (!TwinSceneStatus.InRepo) return;
            var go = _toObject.Invoke(null, new[] { id }) as GameObject;
            if (go == null) return;
            var label = Label(TwinSceneStatus.MarkerFor(go));
            if (label == null) return;
            _style ??= new GUIStyle(EditorStyles.miniLabel) { alignment = TextAnchor.MiddleRight };
            _style.normal.textColor = label.Value.color;
            var content = new GUIContent(label.Value.text, label.Value.tooltip);
            var w = _style.CalcSize(content).x + 4;
            GUI.Label(new Rect(rect.xMax - w, rect.y, w, rect.height), content, _style);
        }
    }
}
