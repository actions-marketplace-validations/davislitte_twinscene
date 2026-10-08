using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace TwinScene.Editor
{
    /// <summary>Window > TwinScene: sync, see what's waiting, settle conflicts, and claim objects.</summary>
    public class TwinSceneWindow : EditorWindow
    {
        Vector2 _scroll;
        bool _showOutgoing, _showSettings;
        string _command;
        string _message;

        [MenuItem("Window/TwinScene")]
        public static void Open() => GetWindow<TwinSceneWindow>("TwinScene");

        void OnEnable()
        {
            TwinSceneStatus.Changed += Repaint;
            _command = EditorPrefs.GetString(TwinSceneCli.CommandPref, "");
        }

        void OnDisable() => TwinSceneStatus.Changed -= Repaint;

        void OnGUI()
        {
            if (!TwinSceneStatus.InRepo)
            {
                EditorGUILayout.HelpBox("This Unity project isn't part of a TwinScene repository: there's no " +
                                        "twinscene.json in this folder or above it. Run 'twinscene init' in the " +
                                        "folder that holds both the Unity and the Godot project.", MessageType.Info);
                if (GUILayout.Button("Look again")) TwinSceneCli.ForgetRoot();
                Settings();
                return;
            }

            Toolbar();
            if (!string.IsNullOrEmpty(_message)) EditorGUILayout.HelpBox(_message, MessageType.None);
            if (TwinSceneStatus.Error != null)
            {
                EditorGUILayout.HelpBox(TwinSceneStatus.Error, MessageType.Error);
                Settings(open: true);
                return;
            }

            var scene = TwinSceneStatus.For(EditorSceneManager.GetActiveScene());
            _scroll = EditorGUILayout.BeginScrollView(_scroll);
            if (scene == null)
                EditorGUILayout.HelpBox("TwinScene doesn't sync the open scene. Scenes are paired by name with the " +
                                        "Godot project (Main.unity <-> Main.tscn), or listed in twinscene.json.",
                                        MessageType.Info);
            else
            {
                Incoming(scene);
                Conflicts(scene);
                Outgoing(scene);
            }
            Claims();
            Settings();
            EditorGUILayout.EndScrollView();
        }

        void Toolbar()
        {
            using (new EditorGUILayout.HorizontalScope(EditorStyles.toolbar))
            {
                using (new EditorGUI.DisabledScope(TwinSceneStatus.Busy))
                {
                    if (GUILayout.Button(new GUIContent("Sync now", "Save, copy changes both ways, and reload."),
                                         EditorStyles.toolbarButton))
                    {
                        _message = "Syncing...";
                        TwinSceneStatus.SyncNow(r => _message = r.Ok ? SyncSummary(r) : null);
                    }
                    if (GUILayout.Button("Refresh", EditorStyles.toolbarButton)) TwinSceneStatus.Refresh();
                }
                GUILayout.FlexibleSpace();
                var when = TwinSceneStatus.LastChecked == System.DateTime.MinValue
                    ? "not checked yet" : "checked " + TwinSceneStatus.LastChecked.ToString("HH:mm:ss");
                GUILayout.Label(TwinSceneStatus.Busy ? "working..." : when, EditorStyles.miniLabel);
            }
        }

        static string SyncSummary(TwinSceneCli.Result r)
        {
            int toGodot = 0, toUnity = 0, conflicts = 0;
            foreach (var s in MiniJson.List(r.Json, "scenes"))
            {
                toGodot += MiniJson.List(s, "to_godot").Count;
                toUnity += MiniJson.List(s, "to_unity").Count;
                conflicts += MiniJson.List(s, "conflicts").Count;
            }
            if (toGodot + toUnity + conflicts == 0) return "Already in sync.";
            var msg = $"Synced: {toUnity} change(s) came in from Godot, {toGodot} went out to Godot.";
            if (conflicts > 0) msg += $" {conflicts} conflict(s) to settle below.";
            return msg + " Commit both projects to share them.";
        }

        void Incoming(TwinSceneStatus.SceneInfo scene)
        {
            var items = scene.Changes.Where(c => c.FromGodot).ToList();
            Header($"Waiting to come in from Godot ({items.Count})");
            if (items.Count == 0)
            {
                EditorGUILayout.LabelField("Nothing: Unity has everything from Godot.", EditorStyles.miniLabel);
                return;
            }
            EditorGUILayout.LabelField("Sync before editing these, or you may end up with conflicts.", EditorStyles.wordWrappedMiniLabel);
            foreach (var c in items) ChangeRow(c, c.GodotPath);
        }

        void Outgoing(TwinSceneStatus.SceneInfo scene)
        {
            var items = scene.Changes.Where(c => !c.FromGodot).ToList();
            _showOutgoing = EditorGUILayout.Foldout(_showOutgoing, $"Your changes going to Godot on the next sync ({items.Count})", true);
            if (!_showOutgoing) return;
            foreach (var c in items) ChangeRow(c, c.UnityPath);
        }

        void ChangeRow(TwinSceneStatus.Change c, string path)
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button(path, EditorStyles.linkLabel)) Select(c.UnityKey);
                GUILayout.FlexibleSpace();
                GUILayout.Label(c.Describe(), EditorStyles.miniLabel);
            }
        }

        void Conflicts(TwinSceneStatus.SceneInfo scene)
        {
            if (scene.Conflicts.Count == 0) return;
            Header($"Conflicts ({scene.Conflicts.Count})");
            EditorGUILayout.HelpBox("These were changed differently in Unity and in Godot. Nothing was overwritten: " +
                                    "pick the version to keep.", MessageType.Warning);
            foreach (var c in scene.Conflicts)
                using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
                {
                    using (new EditorGUILayout.HorizontalScope())
                    {
                        if (GUILayout.Button(c.Path, EditorStyles.linkLabel)) Select(c.UnityKey);
                        GUILayout.FlexibleSpace();
                        GUILayout.Label(c.Field, EditorStyles.boldLabel);
                    }
                    using (new EditorGUILayout.HorizontalScope())
                    {
                        Side("Unity", c.UnityValue, () => TwinSceneStatus.Resolve(c, "unity"), "Keep Unity's");
                        Side("Godot", c.GodotValue, () => TwinSceneStatus.Resolve(c, "godot"), "Use Godot's");
                    }
                }
        }

        void Side(string label, object value, System.Action pick, string button)
        {
            using (new EditorGUILayout.VerticalScope())
            {
                EditorGUILayout.LabelField(label + ": " + MiniJson.Show(value), EditorStyles.wordWrappedMiniLabel);
                using (new EditorGUI.DisabledScope(TwinSceneStatus.Busy))
                    if (GUILayout.Button(button)) pick();
            }
        }

        void Claims()
        {
            var scene = TwinSceneStatus.For(EditorSceneManager.GetActiveScene());
            var list = TwinSceneStatus.Claims.Where(c => scene == null || c.Scene == scene.Name).ToList();
            Header($"Claims ({list.Count})");
            if (TwinSceneStatus.ClaimsError != null)
                EditorGUILayout.HelpBox("Claims need the repository's git remote: " + TwinSceneStatus.ClaimsError, MessageType.None);
            foreach (var c in list)
                using (new EditorGUILayout.HorizontalScope())
                {
                    if (GUILayout.Button(c.UnityPath != "" ? c.UnityPath : c.GodotPath, EditorStyles.linkLabel)) Select(c.UnityKey);
                    GUILayout.FlexibleSpace();
                    GUILayout.Label(c.Mine ? "you" : c.Name, c.Mine ? EditorStyles.miniLabel : EditorStyles.miniBoldLabel);
                }
            var go = Selection.activeGameObject;
            using (new EditorGUILayout.HorizontalScope())
            {
                using (new EditorGUI.DisabledScope(go == null))
                {
                    if (GUILayout.Button(new GUIContent("Claim selected", "Tell the Godot side you're working on it.")))
                        Report(TwinSceneStatus.ClaimObject(go));
                    if (GUILayout.Button("Release selected")) Report(TwinSceneStatus.ClaimObject(go, release: true));
                }
                if (GUILayout.Button("Release all mine")) Report(TwinSceneStatus.ReleaseAll());
            }
        }

        void Report(TwinSceneCli.Result r) => _message = r.Ok ? null : r.Error;

        void Settings(bool open = false)
        {
            _showSettings = EditorGUILayout.Foldout(_showSettings || open, "Settings", true);
            if (!_showSettings) return;
            EditorGUILayout.LabelField("twinscene command (empty: find it automatically)", EditorStyles.miniLabel);
            using (new EditorGUILayout.HorizontalScope())
            {
                _command = EditorGUILayout.TextField(_command);
                if (GUILayout.Button("Save", GUILayout.Width(50)))
                {
                    TwinSceneCli.SetCommand(_command);
                    TwinSceneStatus.Refresh();
                }
            }
            EditorGUILayout.LabelField("Repository: " + (TwinSceneCli.RepoRoot ?? "(none)"), EditorStyles.miniLabel);
        }

        static void Header(string text)
        {
            EditorGUILayout.Space(6);
            EditorGUILayout.LabelField(text, EditorStyles.boldLabel);
        }

        static void Select(string key)
        {
            var go = TwinSceneStatus.Find(key);
            if (go != null)
            {
                Selection.activeGameObject = go;
                EditorGUIUtility.PingObject(go);
            }
        }
    }
}
