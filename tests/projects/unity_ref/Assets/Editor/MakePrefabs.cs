// Builds prefab fixtures with the real Unity: Assets/Prefabs/Crate.prefab (a box with a lid light), Stack.prefab
// (two Crates, nested), and Assets/Scenes/Prefabs.unity placing them in different ways. Run:
//   Unity -batchmode -nographics -projectPath tests/projects/unity_ref -executeMethod MakePrefabs.Build -quit
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class MakePrefabs
{
    public static void Build()
    {
        AssetDatabase.Refresh();
        System.IO.Directory.CreateDirectory("Assets/Prefabs");

        var crate = GameObject.CreatePrimitive(PrimitiveType.Cube);
        crate.name = "Crate";
        crate.GetComponent<MeshRenderer>().sharedMaterial = AssetDatabase.LoadAssetAtPath<Material>("Assets/Materials/BoxRed.mat");
        var lid = new GameObject("Lid");
        lid.transform.SetParent(crate.transform, false);
        lid.transform.localPosition = new Vector3(0, 0.6f, 0);
        lid.transform.localEulerAngles = new Vector3(0, 0, 15);
        var glow = lid.AddComponent<Light>();
        glow.type = LightType.Point;
        glow.range = 3;
        glow.color = new Color(1, 0.8f, 0.3f);
        var cratePrefab = PrefabUtility.SaveAsPrefabAsset(crate, "Assets/Prefabs/Crate.prefab");
        Object.DestroyImmediate(crate);

        var stack = new GameObject("Stack");
        var bottom = (GameObject)PrefabUtility.InstantiatePrefab(cratePrefab, stack.transform);
        bottom.name = "Bottom";
        var top = (GameObject)PrefabUtility.InstantiatePrefab(cratePrefab, stack.transform);
        top.name = "Top";
        top.transform.localPosition = new Vector3(0, 1, 0);
        top.transform.localEulerAngles = new Vector3(0, 45, 0);
        var stackPrefab = PrefabUtility.SaveAsPrefabAsset(stack, "Assets/Prefabs/Stack.prefab");
        Object.DestroyImmediate(stack);

        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var camGo = new GameObject("Main Camera");
        camGo.AddComponent<Camera>().fieldOfView = 60;
        camGo.transform.position = new Vector3(0, 4, -12);
        camGo.transform.eulerAngles = new Vector3(12, 0, 0);
        var sun = new GameObject("Sun").AddComponent<Light>();
        sun.type = LightType.Directional;
        sun.transform.eulerAngles = new Vector3(50, -30, 0);

        var c1 = (GameObject)PrefabUtility.InstantiatePrefab(cratePrefab);
        c1.transform.position = new Vector3(2, 0.5f, 1);
        c1.transform.eulerAngles = new Vector3(0, 30, 0);
        var c2 = (GameObject)PrefabUtility.InstantiatePrefab(cratePrefab);
        c2.name = "Crate Big";
        c2.transform.position = new Vector3(-3, 1, 0);
        c2.transform.localScale = new Vector3(2, 2, 2);
        var shelf = new GameObject("Shelf");
        shelf.transform.position = new Vector3(0, 0, 4);
        var c3 = (GameObject)PrefabUtility.InstantiatePrefab(cratePrefab, shelf.transform);
        c3.name = "Crate On Shelf";
        c3.transform.localPosition = new Vector3(0, 2, 0);
        var sticker = new GameObject("Sticker");  // added to a placed prefab, in the scene only
        sticker.transform.SetParent(c3.transform, false);
        sticker.transform.localPosition = new Vector3(0, 0, -0.51f);
        var s1 = (GameObject)PrefabUtility.InstantiatePrefab(stackPrefab);
        s1.transform.position = new Vector3(5, 0, -2);

        // Edits inside placed prefabs (overrides): a red, brighter, raised lid; a hidden lid; another material.
        var bigLid = c2.transform.Find("Lid");
        bigLid.localPosition = new Vector3(0, 0.8f, 0);
        var bigGlow = bigLid.GetComponent<Light>();
        bigGlow.color = new Color(1, 0.1f, 0.1f);
        bigGlow.intensity = 3;
        PrefabUtility.RecordPrefabInstancePropertyModifications(bigLid);
        PrefabUtility.RecordPrefabInstancePropertyModifications(bigGlow);
        var hiddenLid = c1.transform.Find("Lid").gameObject;
        hiddenLid.SetActive(false);
        PrefabUtility.RecordPrefabInstancePropertyModifications(hiddenLid);
        var green = new Material(Shader.Find("Standard")) { color = new Color(0.2f, 0.8f, 0.3f) };
        AssetDatabase.CreateAsset(green, "Assets/Materials/Green.mat");
        var shelfRenderer = c3.GetComponent<MeshRenderer>();
        shelfRenderer.sharedMaterial = green;
        PrefabUtility.RecordPrefabInstancePropertyModifications(shelfRenderer);

        System.IO.Directory.CreateDirectory("Assets/Scenes");
        EditorSceneManager.SaveScene(scene, "Assets/Scenes/Prefabs.unity");
        AssetDatabase.SaveAssets();
        Debug.Log("TWINSCENE_PREFABS saved");
    }
}
