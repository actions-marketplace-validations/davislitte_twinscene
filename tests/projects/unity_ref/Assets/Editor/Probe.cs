// Opens a scene in the real Unity and writes where everything ends up, for tests/tools/compare_probes.py.
// Run: Unity -batchmode -nographics -projectPath <project> -executeMethod Probe.Run -quit
//      with TWINSCENE_PROBE_SCENE (Assets/...unity) and TWINSCENE_PROBE_OUT (a .json path) set.
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class Probe
{
    const float Aspect = 1600f / 900f;

    // TWINSCENE_PROBE_SCENE and TWINSCENE_PROBE_OUT can list several scenes (separated by ;).
    // TWINSCENE_PROBE_INTERNALS=1 also reports the objects inside placed prefabs.
    public static void Run()
    {
        var scenes = System.Environment.GetEnvironmentVariable("TWINSCENE_PROBE_SCENE").Split(';');
        var outs = System.Environment.GetEnvironmentVariable("TWINSCENE_PROBE_OUT").Split(';');
        AssetDatabase.Refresh();
        for (int i = 0; i < scenes.Length; i++) ProbeScene(scenes[i], outs[i]);
    }

    static void ProbeScene(string scenePath, string outPath)
    {
        var internals = System.Environment.GetEnvironmentVariable("TWINSCENE_PROBE_INTERNALS") == "1";
        var scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
        var cam = scene.GetRootGameObjects().SelectMany(r => r.GetComponentsInChildren<Camera>(true)).FirstOrDefault();
        if (cam != null) cam.aspect = Aspect;
        var sb = new StringBuilder("{\n");
        var first = true;
        foreach (var root in scene.GetRootGameObjects())
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                if (t.GetComponentsInParent<Transform>(true).Any(a => a.GetComponent("TwinScene.TwinScenePart") != null))
                    continue;  // TwinScene helpers, and anything inside them
                var go = t.gameObject;
                // inside a placed model or prefab, only its root is a TwinScene object (with internals: inside
                // placed prefabs too, but never inside imported model files)
                if (PrefabUtility.IsPartOfPrefabInstance(go) && !PrefabUtility.IsAnyPrefabInstanceRoot(go) &&
                    (!internals || InsideModel(go))) continue;
                if (go.GetComponent<RectTransform>() != null) continue;  // UI isn't synced
                if (go.GetComponentsInParent<Transform>(true).Any(a => (a.gameObject.hideFlags & HideFlags.HideInHierarchy) != 0))
                    continue;  // editor-only objects hidden from the Hierarchy aren't synced
                var e = new List<string>();
                var type = go.GetComponent<Camera>() ? "Camera" : go.GetComponent<Light>() ? "Light" :
                    go.GetComponent<Rigidbody>() ? "Rigidbody" : go.GetComponent<MeshFilter>() ? "Mesh" :
                    go.GetComponent<Collider>() ? "Collider" : "GameObject";
                e.Add($"\"type\": \"{type}\"");
                var lt = go.GetComponent<Light>();
                if (lt != null)
                    e.Add($"\"light\": [{F(lt.color.r)}, {F(lt.color.g)}, {F(lt.color.b)}, {F(lt.intensity)}, " +
                          $"{F(lt.type == LightType.Directional ? 0 : lt.range)}]");
                e.Add($"\"pos\": {V(t.position)}");
                e.Add($"\"active\": {(go.activeInHierarchy ? "true" : "false")}");
                e.Add($"\"look\": {V(t.forward)}");
                e.Add($"\"up\": {V(t.up)}");
                if (cam != null)
                {
                    var vp = cam.WorldToViewportPoint(t.position);
                    if (vp.z > 0) e.Add($"\"screen\": [{F(vp.x)}, {F(vp.y)}]");
                }
                // all the meshes of the object: its own, its mesh helper's, or a placed model's
                var rends = new List<Renderer>();
                if (go.GetComponent<Renderer>() != null) rends.Add(go.GetComponent<Renderer>());
                foreach (Transform k in t)
                    if (k.GetComponent("TwinScene.TwinScenePart") != null && k.name.EndsWith("(mesh)"))
                        rends.AddRange(k.GetComponentsInChildren<Renderer>(true));
                if (rends.Count == 0 && PrefabUtility.IsAnyPrefabInstanceRoot(go) && (!internals || IsModel(go)))
                    rends.AddRange(go.GetComponentsInChildren<Renderer>(true));
                if (rends.Count > 0)
                {
                    var b = rends[0].bounds;
                    foreach (var x in rends) b.Encapsulate(x.bounds);
                    e.Add($"\"bounds\": [{F(b.min.x)}, {F(b.min.y)}, {F(b.min.z)}, {F(b.max.x)}, {F(b.max.y)}, {F(b.max.z)}]");
                }
                sb.Append(first ? "" : ",\n").Append($" \"{Path(t)}\": {{{string.Join(", ", e)}}}");
                first = false;
            }
        sb.Append("\n}\n");
        System.IO.File.WriteAllText(outPath, sb.ToString());
        Debug.Log("TWINSCENE_PROBE wrote " + outPath);
    }

    // Godot needs unique names among siblings, so TwinScene calls the second "eye" "eye2": do the same here.
    static string Path(Transform t) => t.parent == null ? Unique(t) : Path(t.parent) + "/" + Unique(t);

    static string Unique(Transform t)
    {
        var siblings = t.parent != null ? Enumerable.Range(0, t.parent.childCount).Select(i => t.parent.GetChild(i))
                                        : t.gameObject.scene.GetRootGameObjects().Select(g => g.transform);
        int n = 0;
        foreach (var s in siblings)
        {
            if (s.name == t.name) n++;
            if (s == t) break;
        }
        return n <= 1 ? t.name : t.name + n;
    }

    static bool IsModel(GameObject go)
    {
        var src = PrefabUtility.GetCorrespondingObjectFromOriginalSource(go);
        var path = src != null ? AssetDatabase.GetAssetPath(src) : "";
        return !path.EndsWith(".prefab");
    }

    static bool InsideModel(GameObject go)
    {
        for (var t = go.transform; t != null; t = t.parent)
            if (PrefabUtility.IsAnyPrefabInstanceRoot(t.gameObject) && IsModel(t.gameObject)) return true;
        return false;
    }
    static string F(float f) => f.ToString("R", CultureInfo.InvariantCulture);
    static string V(Vector3 v) => $"[{F(v.x)}, {F(v.y)}, {F(v.z)}]";
}
