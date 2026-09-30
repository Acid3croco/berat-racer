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
        PlayerSettings.defaultScreenWidth = 1600; PlayerSettings.defaultScreenHeight = 900; PlayerSettings.resizableWindow = true;
        PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
        PlayerSettings.runInBackground = true;
        Directory.CreateDirectory("Assets/Scenes");
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        EditorSceneManager.SaveScene(scene, "Assets/Scenes/Main.unity");
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene("Assets/Scenes/Main.unity", true) };
        AssetDatabase.SaveAssets();
        Debug.Log("[berat] project set up");
    }

    /// <summary>Build target folder. Pass "-out Path/To.app" on the command line to build somewhere else (e.g. next to a running copy).</summary>
    static string OutputPath(string fallback)
    {
        var a = System.Environment.GetCommandLineArgs(); int i = System.Array.IndexOf(a, "-out");
        return Path.GetFullPath(i >= 0 && i + 1 < a.Length ? a[i + 1] : fallback);
    }

    [MenuItem("Berat/Build macOS")]
    public static void BuildMac()
    {
        string app = OutputPath("../Build/BeratRacer.app");
        Build(BuildTarget.StandaloneOSX, app, Path.Combine(app, "Contents", "Resources", "Data", "StreamingAssets"));
    }

    /// <summary>Windows x64 player (Mono, cross-built from macOS with the "Windows Build Support (Mono)" module): Build/win/BeratRacer.exe + "Berat Racer_Data".</summary>
    [MenuItem("Berat/Build Windows")]
    public static void BuildWindows()
    {
        string exe = OutputPath("../Build/win/BeratRacer.exe");
        Build(BuildTarget.StandaloneWindows64, exe, Path.Combine(Path.GetDirectoryName(exe), Path.GetFileNameWithoutExtension(exe) + "_Data", "StreamingAssets"));
    }

    static void Build(BuildTarget target, string output, string streamingAssets)
    {
        Setup();
        var opts = new BuildPlayerOptions { scenes = new[] { "Assets/Scenes/Main.unity" }, locationPathName = output, target = target, options = BuildOptions.None };
        var report = BuildPipeline.BuildPlayer(opts);
        Debug.Log($"[berat] build {target} {report.summary.result} {report.summary.totalSize / 1048576} MB");
        if (report.summary.result != UnityEditor.Build.Reporting.BuildResult.Succeeded) { EditorApplication.Exit(1); return; }
        CopyWorld(streamingAssets);
    }

    /// <summary>World packed into the player: repo-root world/, or "-world path/to/world_x" on the command line (e.g. to ship a release with another map).</summary>
    static string WorldPath()
    {
        var a = System.Environment.GetCommandLineArgs(); int i = System.Array.IndexOf(a, "-world");
        return Path.GetFullPath(i >= 0 && i + 1 < a.Length ? a[i + 1] : "../world");
    }

    /// <summary>The generated world lives outside Assets/ so Unity never imports its tens of thousands of files; copy it into the player. APFS clones make this instant.</summary>
    static void CopyWorld(string streamingAssets)
    {
        string src = WorldPath(), dst = Path.Combine(streamingAssets, "berat");
        if (!File.Exists(Path.Combine(src, "far.bin"))) { Debug.LogWarning("[berat] no generated world at " + src + " (run tools/build_world.py); the player will not start"); return; }
        Directory.CreateDirectory(Path.GetDirectoryName(dst));
        if (Directory.Exists(dst)) Directory.Delete(dst, true);
        var p = System.Diagnostics.Process.Start("/bin/cp", $"-Rc \"{src}\" \"{dst}\"");
        p.WaitForExit();
        if (p.ExitCode != 0) { p = System.Diagnostics.Process.Start("/bin/cp", $"-R \"{src}\" \"{dst}\""); p.WaitForExit(); }
        Debug.Log($"[berat] world copied into the player ({p.ExitCode})");
    }
}
