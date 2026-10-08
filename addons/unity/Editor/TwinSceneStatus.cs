using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace TwinScene.Editor
{
    /// <summary>
    /// What twinscene says about this repository right now: changes waiting on either side, conflicts and claims.
    /// Refreshed in the background when scenes open or save, when Unity gets focus, and every minute.
    /// </summary>
    [InitializeOnLoad]
    public static class TwinSceneStatus
    {
        public class Change
        {
            public string Direction, Kind, UnityKey, GodotKey, UnityPath, GodotPath;
            public string[] Fields;
            public bool FromGodot => Direction == "to_unity";
            public string Describe() => Kind == "changed" ? string.Join(", ", Fields) : Kind;
        }

        public class Conflict
        {
            public string Scene, Path, Field, UnityKey, GodotKey;
            public object UnityValue, GodotValue;
        }

        public class Claim
        {
            public string Scene, UnityKey, GodotKey, UnityPath, GodotPath, Who, When;
            public bool Mine;
            public string Name => Who.Contains("<") ? Who.Substring(0, Who.IndexOf('<')).Trim() : Who;
        }

        public class SceneInfo
        {
            public string Name, UnityScene, GodotScene;
            public List<Change> Changes = new List<Change>();
            public List<Conflict> Conflicts = new List<Conflict>();
        }

        /// <summary>Everything TwinScene knows about one GameObject.</summary>
        public class Marker
        {
            public Change FromGodot;  // changed or deleted in Godot, not synced in yet
            public Change ToGodot;    // changed here, not synced out yet
            public List<Conflict> Conflicts = new List<Conflict>();
            public Claim Claim;
            public string HelperPart;
            public bool ClaimedByOther => Claim != null && !Claim.Mine;
            public bool Any => FromGodot != null || Conflicts.Count > 0 || ClaimedByOther || HelperPart != null;
        }

        public static List<SceneInfo> Scenes = new List<SceneInfo>();
        public static List<Claim> Claims = new List<Claim>();
        public static string Me = "", Error, ClaimsError;
        public static DateTime LastChecked = DateTime.MinValue;
        public static bool Busy;
        public static event Action Changed;
        static double _nextAuto;

        static TwinSceneStatus()
        {
            if (Application.isBatchMode) return;
            EditorApplication.delayCall += () => Refresh();
            EditorSceneManager.sceneOpened += (s, m) => Refresh();
            EditorSceneManager.sceneSaved += s => Refresh();
            EditorApplication.focusChanged += focused => { if (focused) Refresh(); };
            EditorApplication.update += () =>
            {
                if (EditorApplication.timeSinceStartup > _nextAuto) Refresh();
            };
        }

        public static bool InRepo => TwinSceneCli.RepoRoot != null;

        // ------------------------------------------------------------ refresh

        /// <summary>Ask twinscene for the current status in the background (with claims, which needs the network).</summary>
        public static void Refresh()
        {
            _nextAuto = EditorApplication.timeSinceStartup + 60;
            if (Busy || !InRepo) return;
            Busy = true;
            TwinSceneCli.RunAsync(r => { Busy = false; Apply(r); }, "status", "--json", "--claims");
        }

        /// <summary>The same, but wait for it.</summary>
        public static TwinSceneCli.Result RefreshNow()
        {
            var r = TwinSceneCli.Run("status", "--json", "--claims");
            Apply(r);
            return r;
        }

        public static void Apply(TwinSceneCli.Result r)
        {
            LastChecked = DateTime.Now;
            Error = r.Error;
            if (r.Json != null && r.Error == null)
            {
                Scenes = MiniJson.List(r.Json, "scenes").Select(ReadScene).ToList();
                Me = MiniJson.Text(r.Json, "me");
                ClaimsError = MiniJson.Text(r.Json, "claims_error");
                if (ClaimsError == "") ClaimsError = null;
                if (MiniJson.List(r.Json, "claims") is List<object> cl && (cl.Count > 0 || ClaimsError == null))
                    Claims = cl.Select(ReadClaim).ToList();
            }
            Changed?.Invoke();
            EditorApplication.RepaintHierarchyWindow();
        }

        static SceneInfo ReadScene(object o)
        {
            var s = new SceneInfo
            {
                Name = MiniJson.Text(o, "scene"),
                UnityScene = MiniJson.Text(o, "unity_scene"),
                GodotScene = MiniJson.Text(o, "godot_scene"),
            };
            foreach (var c in MiniJson.List(o, "changes"))
                s.Changes.Add(new Change
                {
                    Direction = MiniJson.Text(c, "direction"), Kind = MiniJson.Text(c, "kind"),
                    UnityKey = MiniJson.Text(c, "unity_key"), GodotKey = MiniJson.Text(c, "godot_key"),
                    UnityPath = MiniJson.Text(c, "unity_path"), GodotPath = MiniJson.Text(c, "godot_path"),
                    Fields = MiniJson.List(c, "fields").Select(f => f.ToString()).ToArray(),
                });
            foreach (var c in MiniJson.List(o, "conflicts"))
            {
                var d = (Dictionary<string, object>)c;
                s.Conflicts.Add(new Conflict
                {
                    Scene = s.Name, Path = MiniJson.Text(c, "path"), Field = MiniJson.Text(c, "field"),
                    UnityKey = MiniJson.Text(c, "unity_key"), GodotKey = MiniJson.Text(c, "godot_key"),
                    UnityValue = d.TryGetValue("unity", out var u) ? u : null,
                    GodotValue = d.TryGetValue("godot", out var g) ? g : null,
                });
            }
            return s;
        }

        static Claim ReadClaim(object o)
        {
            var who = MiniJson.Text(o, "who");
            return new Claim
            {
                Scene = MiniJson.Text(o, "scene"), UnityKey = MiniJson.Text(o, "unity_key"),
                GodotKey = MiniJson.Text(o, "godot_key"), UnityPath = MiniJson.Text(o, "unity_path"),
                GodotPath = MiniJson.Text(o, "godot_path"), Who = who, When = MiniJson.Text(o, "when"),
                Mine = who == Me,
            };
        }

        // ------------------------------------------------------------ lookups

        /// <summary>The TwinScene scene for a Unity scene (null if TwinScene doesn't sync it).</summary>
        public static SceneInfo For(Scene scene) =>
            Scenes.FirstOrDefault(s => string.Equals(s.UnityScene, scene.path, StringComparison.OrdinalIgnoreCase));

        /// <summary>A GameObject's TwinScene key: u:&lt;its file id in the scene&gt; (null until the scene is saved).</summary>
        public static string KeyOf(GameObject go)
        {
            if (go == null || string.IsNullOrEmpty(go.scene.path)) return null;
            var id = GlobalObjectId.GetGlobalObjectIdSlow(go);
            if (id.targetPrefabId != 0)  // inside a placed model or prefab: only its root is a TwinScene object
                return PrefabUtility.IsAnyPrefabInstanceRoot(go) ? "u:" + unchecked((long)id.targetPrefabId) : null;
            return id.targetObjectId == 0 ? null : "u:" + unchecked((long)id.targetObjectId);
        }

        public static string PathOf(GameObject go)
        {
            var t = go.transform;
            var path = t.name;
            while ((t = t.parent) != null) path = t.name + "/" + path;
            return path;
        }

        public static Marker MarkerFor(GameObject go)
        {
            var m = new Marker();
            var part = go.GetComponent<TwinScenePart>();
            if (part != null) m.HelperPart = part.part;
            var key = KeyOf(go);
            var scene = For(go.scene);
            if (key == null || scene == null) return m;
            foreach (var c in scene.Changes)
                if (c.UnityKey == key)
                {
                    if (c.FromGodot) m.FromGodot = c;
                    else m.ToGodot = c;
                }
            m.Conflicts = scene.Conflicts.Where(c => c.UnityKey == key).ToList();
            m.Claim = Claims.FirstOrDefault(c => c.Scene == scene.Name && c.UnityKey == key);
            return m;
        }

        // ------------------------------------------------------------ actions

        /// <summary>Save, sync both ways, and reload the open scenes so the other side's changes show up.</summary>
        public static void SyncNow(Action<TwinSceneCli.Result> done = null, params string[] extra)
        {
            if (!SaveScenes()) return;
            var open = OpenScenePaths();
            Busy = true;
            TwinSceneCli.RunAsync(r =>
            {
                Busy = false;
                AfterSync(r, open);
                done?.Invoke(r);
            }, new[] { "sync", "--json" }.Concat(extra).ToArray());
        }

        public static void Resolve(Conflict c, string side, Action<TwinSceneCli.Result> done = null) =>
            SyncNow(done, "--resolve", $"{c.UnityKey}:{c.Field}={side}");

        static void AfterSync(TwinSceneCli.Result r, List<string> open)
        {
            if (!r.Ok)
            {
                Error = r.Error;
                Changed?.Invoke();
                return;
            }
            AssetDatabase.Refresh();
            for (int i = 0; i < open.Count; i++)
                if (System.IO.File.Exists(open[i]))
                    EditorSceneManager.OpenScene(open[i], i == 0 ? OpenSceneMode.Single : OpenSceneMode.Additive);
            if (Application.isBatchMode) RefreshNow();
            else Refresh();
        }

        static bool SaveScenes()
        {
            if (Application.isBatchMode) return EditorSceneManager.SaveOpenScenes();
            return EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo();
        }

        static List<string> OpenScenePaths()
        {
            var paths = new List<string>();
            var active = SceneManager.GetActiveScene().path;
            if (!string.IsNullOrEmpty(active)) paths.Add(active);
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var p = SceneManager.GetSceneAt(i).path;
                if (!string.IsNullOrEmpty(p) && !paths.Contains(p)) paths.Add(p);
            }
            return paths;
        }

        public static TwinSceneCli.Result ClaimObject(GameObject go, bool release = false)
        {
            var scene = For(go.scene);
            var key = KeyOf(go);
            if (scene == null || key == null)
                return new TwinSceneCli.Result { Error = "Save and sync the scene first, so both sides know this object." };
            var r = TwinSceneCli.Run(release ? "release" : "claim", "--scene", scene.Name, "--key", key, "--json");
            if (r.Ok) ApplyClaims(r);
            return r;
        }

        public static TwinSceneCli.Result ReleaseAll()
        {
            var r = TwinSceneCli.Run("release", "--all", "--json");
            if (r.Ok) ApplyClaims(r);
            return r;
        }

        static void ApplyClaims(TwinSceneCli.Result r)
        {
            Me = MiniJson.Text(r.Json, "me");
            Claims = MiniJson.List(r.Json, "claims").Select(ReadClaim).ToList();
            Changed?.Invoke();
            EditorApplication.RepaintHierarchyWindow();
        }

        /// <summary>Find a GameObject in the open scenes by its TwinScene key.</summary>
        public static GameObject Find(string key)
        {
            for (int i = 0; i < SceneManager.sceneCount; i++)
                foreach (var root in SceneManager.GetSceneAt(i).GetRootGameObjects())
                    foreach (var t in root.GetComponentsInChildren<Transform>(true))
                        if (KeyOf(t.gameObject) == key) return t.gameObject;
            return null;
        }
    }
}
