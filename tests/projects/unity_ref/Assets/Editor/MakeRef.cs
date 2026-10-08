// Builds Assets/Scenes/Reference.unity with the real Unity, so the fixture is Unity's own output, and prints where
// Unity put the axis marker's points. Run:
//   Unity -batchmode -nographics -projectPath tests/projects/unity_ref -executeMethod MakeRef.Build -quit
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class MakeRef
{
    public static void Build()
    {
        AssetDatabase.Refresh();
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

        // The same box as the Godot reference: at (1, 2, 3), turned 30 degrees around Y.
        var box = GameObject.CreatePrimitive(PrimitiveType.Cube);
        box.name = "Box";
        box.transform.localPosition = new Vector3(1, 2, 3);
        box.transform.localEulerAngles = new Vector3(0, 30, 0);
        var mat = new Material(Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard"));
        mat.color = new Color(0.8f, 0.2f, 0.1f);
        mat.SetFloat("_Metallic", 0.3f);
        mat.SetFloat("_Smoothness", 0.4f);
        mat.SetFloat("_Glossiness", 0.4f);
        System.IO.Directory.CreateDirectory("Assets/Materials");
        AssetDatabase.CreateAsset(mat, "Assets/Materials/BoxRed.mat");
        box.GetComponent<MeshRenderer>().sharedMaterial = mat;
        Object.DestroyImmediate(box.GetComponent<BoxCollider>());

        var child = new GameObject("Child");
        child.transform.SetParent(box.transform, false);
        child.transform.localPosition = new Vector3(0, 1, 0);
        child.transform.localEulerAngles = new Vector3(10, 20, 30);
        child.transform.localScale = new Vector3(1, 2, 3);

        var camGo = new GameObject("Main Camera");
        camGo.tag = "MainCamera";
        var cam = camGo.AddComponent<Camera>();
        cam.fieldOfView = 60;
        cam.nearClipPlane = 0.3f;
        cam.farClipPlane = 1000;
        camGo.transform.localPosition = new Vector3(0, 1, -10);

        var sunGo = new GameObject("Sun");
        var sun = sunGo.AddComponent<Light>();
        sun.type = LightType.Directional;
        sun.intensity = 1.2f;
        sun.color = new Color(1, 0.95f, 0.85f);
        sun.shadows = LightShadows.Soft;
        sunGo.transform.localEulerAngles = new Vector3(50, -30, 0);

        var lampGo = new GameObject("Lamp");
        var lamp = lampGo.AddComponent<Light>();
        lamp.type = LightType.Point;
        lamp.range = 8;
        lampGo.transform.localPosition = new Vector3(-2, 3, 0);

        var spotGo = new GameObject("Spot");
        var spot = spotGo.AddComponent<Light>();
        spot.type = LightType.Spot;
        spot.range = 12;
        spot.spotAngle = 60;
        spotGo.transform.localPosition = new Vector3(0, 4, -2);

        var floor = new GameObject("Floor");
        floor.isStatic = true;
        var floorShape = new GameObject("Shape");
        floorShape.transform.SetParent(floor.transform, false);
        floorShape.transform.localPosition = new Vector3(0, -0.5f, 0);
        floorShape.AddComponent<BoxCollider>().size = new Vector3(20, 1, 20);

        var ball = new GameObject("Ball");
        ball.transform.localPosition = new Vector3(0, 5, 0);
        ball.AddComponent<Rigidbody>().mass = 2.5f;
        ball.AddComponent<SphereCollider>().radius = 0.75f;
        var pill = new GameObject("Pill");
        pill.transform.SetParent(ball.transform, false);
        var cap = pill.AddComponent<CapsuleCollider>();
        cap.radius = 0.4f;
        cap.height = 1.8f;

        var hidden = new GameObject("Hidden Thing");
        hidden.SetActive(false);

        // The shared axis marker, placed at the origin with no rotation.
        var markerAsset = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Shared/axis_marker.obj");
        if (markerAsset != null)
        {
            var marker = (GameObject)PrefabUtility.InstantiatePrefab(markerAsset);
            marker.name = "Axis Marker";
            var mesh = markerAsset.GetComponentInChildren<MeshFilter>().sharedMesh;
            var world = mesh.vertices.Select(v => markerAsset.GetComponentInChildren<MeshFilter>().transform.TransformPoint(v));
            Debug.Log("TWINSCENE_AXIS unity " + string.Join(" ", world.Select(v => $"({v.x:0.###},{v.y:0.###},{v.z:0.###})")));
        }
        else Debug.LogError("TWINSCENE_AXIS marker not imported");

        System.IO.Directory.CreateDirectory("Assets/Scenes");
        EditorSceneManager.SaveScene(scene, "Assets/Scenes/Reference.unity");
        AssetDatabase.SaveAssets();
        Debug.Log("TWINSCENE_REF saved");
    }
}
