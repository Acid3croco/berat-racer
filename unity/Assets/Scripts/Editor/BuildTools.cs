using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildTools
{
    [MenuItem("Berat/Setup Project")]
    public static void Setup()
    {
        PlayerSettings.productName = "Berat Racer"; PlayerSettings.companyName = "local";
        PlayerSettings.colorSpace = ColorSpace.Gamma;
        PlayerSettings.defaultScreenWidth = 1920; PlayerSettings.defaultScreenHeight = 1080;
        PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
        PlayerSettings.runInBackground = true;
        Directory.CreateDirectory("Assets/Scenes");
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        EditorSceneManager.SaveScene(scene, "Assets/Scenes/Main.unity");
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene("Assets/Scenes/Main.unity", true) };
        AssetDatabase.SaveAssets();
        Debug.Log("[berat] project set up");
    }

    [MenuItem("Berat/Build macOS")]
    public static void BuildMac()
    {
        Setup();
        var opts = new BuildPlayerOptions
        {
            scenes = new[] { "Assets/Scenes/Main.unity" },
            locationPathName = Path.GetFullPath("../Build/BeratRacer.app"),
            target = BuildTarget.StandaloneOSX, options = BuildOptions.None
        };
        var report = BuildPipeline.BuildPlayer(opts);
        Debug.Log($"[berat] build {report.summary.result} {report.summary.totalSize / 1048576} MB");
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded) EditorApplication.Exit(1);
    }
}
