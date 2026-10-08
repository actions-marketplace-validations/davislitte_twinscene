// Exercises the TwinScene Unity add-on inside the real editor. Copied into a test project by
// tests/tools/check_unity_addon.py, which prepares a repository where Godot moved Box/Child and resized Box, both
// sides moved Lamp (a conflict), and "Bo" claimed Ball. Prints "CHECK PASS/FAIL ..." lines.
using System;
using System.Linq;
using TwinScene;
using TwinScene.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class TwinSceneAddonCheck
{
    static int _fails;

    static void Check(bool ok, string what)
    {
        Debug.Log((ok ? "CHECK PASS " : "CHECK FAIL ") + what);
        if (!ok) _fails++;
    }

    static bool Near(Vector3 a, Vector3 b) => (a - b).magnitude < 1e-3f;

    public static void Run()
    {
        try
        {
            EditorSceneManager.OpenScene("Assets/Scenes/Reference.unity");
            Check(TwinSceneStatus.InRepo, "finds twinscene.json above the Unity project");
            Check(TwinSceneHierarchy.Hooked, "hierarchy labels hooked into this Unity's Hierarchy window");
            var r = TwinSceneStatus.RefreshNow();
            Check(r.Ok, "status runs through the twinscene command " + (r.Error ?? ""));
            var scene = TwinSceneStatus.For(SceneManager.GetActiveScene());
            Check(scene != null, "the open scene is one TwinScene syncs");
            Check(TwinSceneStatus.Me.StartsWith("Ana"), "knows who I am: " + TwinSceneStatus.Me);

            // -- waiting changes from Godot
            var child = GameObject.Find("Box/Child");
            var m = TwinSceneStatus.MarkerFor(child);
            Check(m.FromGodot != null && m.FromGodot.Fields.Contains("position"), "Box/Child: Godot's move is waiting");
            Check(TwinSceneHierarchy.Label(m)?.text == "Godot changed", "Box/Child: hierarchy says 'Godot changed'");
            Check(TwinSceneWatcher.WarningFor(child)?.Contains("changed in Godot") == true, "Box/Child: editing it warns");
            Check(scene.Changes.Count(c => c.FromGodot) == 2, "two changes waiting from Godot (Box, Box/Child)");

            // -- conflict
            var lamp = GameObject.Find("Lamp");
            var lm = TwinSceneStatus.MarkerFor(lamp);
            Check(lm.Conflicts.Count == 1 && lm.Conflicts[0].Field == "position", "Lamp: position conflict found");
            Check(TwinSceneHierarchy.Label(lm)?.text == "conflict", "Lamp: hierarchy says 'conflict'");

            // -- claims
            var ball = GameObject.Find("Ball");
            var bm = TwinSceneStatus.MarkerFor(ball);
            Check(bm.ClaimedByOther && bm.Claim.Name == "Bo", "Ball: claimed by Bo");
            Check(TwinSceneHierarchy.Label(bm)?.text == "claimed: Bo", "Ball: hierarchy says 'claimed: Bo'");
            Check(TwinSceneWatcher.WarningFor(ball)?.Contains("claimed by Bo") == true, "Ball: editing it warns");
            var taken = TwinSceneStatus.ClaimObject(ball);
            Check(!taken.Ok && taken.Error.Contains("already claimed"), "Ball: can't claim Bo's object");
            var sun = GameObject.Find("Sun");
            var mine = TwinSceneStatus.ClaimObject(sun);
            Check(mine.Ok && TwinSceneStatus.MarkerFor(sun).Claim?.Mine == true, "Sun: I can claim it " + (mine.Error ?? ""));
            Check(TwinSceneHierarchy.Label(TwinSceneStatus.MarkerFor(sun))?.text == "claimed: you", "Sun: hierarchy says 'claimed: you'");

            // -- inspector coverage
            var (synced, only) = TwinSceneInspector.Coverage(ball);
            Check(synced.Contains("Rigidbody") && synced.Contains("Sphere Collider") && only == "", "Ball: inspector lists synced components");
            var (_, camOnly) = TwinSceneInspector.Coverage(GameObject.Find("Main Camera"));
            Check(camOnly == "", "Main Camera: nothing Unity-only");

            // -- settle the conflict with Godot's value (this also syncs everything else)
            TwinSceneStatus.Resolve(lm.Conflicts[0], "godot");
            lamp = GameObject.Find("Lamp");
            Check(Near(lamp.transform.localPosition, new Vector3(0, 0, 5)), "Lamp: took Godot's position " + lamp.transform.localPosition);
            child = GameObject.Find("Box/Child");
            Check(Near(child.transform.localPosition, new Vector3(0, 3, 0)), "Box/Child: Godot's move came in " + child.transform.localPosition);
            scene = TwinSceneStatus.For(SceneManager.GetActiveScene());
            Check(scene.Changes.Count == 0 && scene.Conflicts.Count == 0, "nothing left to sync");

            // -- the resized box arrives as a helper object
            var helper = GameObject.Find("Box").GetComponentsInChildren<TwinScenePart>(true).FirstOrDefault();
            Check(helper != null && helper.part == "mesh", "Box: Godot's resize arrived as a mesh helper");
            if (helper != null)
            {
                Check(Near(helper.transform.localScale, new Vector3(2, 1, 0.5f)), "Box helper: scaled 2 x 1 x 0.5");
                Check(TwinSceneHierarchy.Label(TwinSceneStatus.MarkerFor(helper.gameObject))?.text == "helper", "Box helper: hierarchy says 'helper'");
                Check(TwinSceneWatcher.WarningFor(helper.gameObject)?.Contains("helper") == true, "Box helper: editing it warns");
                var editor = UnityEditor.Editor.CreateEditor(helper);
                Check(editor is TwinScenePartEditor, "Box helper: has its own inspector");
                UnityEngine.Object.DestroyImmediate(editor);
            }

            var released = TwinSceneStatus.ReleaseAll();
            Check(released.Ok && TwinSceneStatus.Claims.All(c => !c.Mine), "release all my claims");
            Check(TwinSceneStatus.Claims.Any(c => c.Name == "Bo"), "Bo's claim is still there");
        }
        catch (Exception e)
        {
            Check(false, "unexpected error: " + e);
        }
        Debug.Log($"CHECK DONE {_fails} failed");
        EditorApplication.Exit(_fails == 0 ? 0 : 1);
    }
}
