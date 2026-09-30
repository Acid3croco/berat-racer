using System;
using System.IO;
using UnityEngine;

/// <summary>Timestamped log to the Unity console/Player.log AND berat.log (easy to tail): ~/Library/Logs/BeratRacer on macOS, %LOCALAPPDATA%\BeratRacer on Windows.</summary>
public static class Log
{
    static StreamWriter file;
    static readonly System.Diagnostics.Stopwatch clock = System.Diagnostics.Stopwatch.StartNew();
    public static string Path_ { get; private set; }

    public static void Init()
    {
        if (file != null) return;
        try
        {
            string dir = Application.platform == RuntimePlatform.WindowsPlayer
                ? System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BeratRacer")
                : System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Personal), "Library/Logs/BeratRacer");
            Directory.CreateDirectory(dir);
            string name = Application.isBatchMode ? "berat.batch" : "berat";        // automated headless runs must not clobber the log of a game being played
            var args = Environment.GetCommandLineArgs(); int li = Array.IndexOf(args, "-logname");
            if (li >= 0 && li + 1 < args.Length) name = args[li + 1];                  // e.g. two players on one machine
            Path_ = System.IO.Path.Combine(dir, name + ".log");
            string prev = System.IO.Path.Combine(dir, name + ".prev.log");
            if (File.Exists(Path_)) File.Copy(Path_, prev, true);
            file = new StreamWriter(new FileStream(Path_, FileMode.Create, FileAccess.Write, FileShare.ReadWrite)) { AutoFlush = true };
        }
        catch (Exception e) { Debug.LogWarning("[log] cannot open log file: " + e.Message); }
        Application.logMessageReceived += (msg, stack, type) =>
        {
            if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert)
                Write("UNITY-" + type, msg + "\n" + stack);
        };
    }

    public static void I(string tag, string msg) => Write(tag, msg);
    static void Write(string tag, string msg)
    {
        string line = $"[{clock.Elapsed.TotalSeconds,8:F2}s f{Time.frameCount,-6}] [{tag}] {msg}";
        if (!tag.StartsWith("UNITY-")) Debug.Log(line);
        try { file?.WriteLine(line); } catch { /* logging must never crash the game */ }
    }
}
