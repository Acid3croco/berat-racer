using System.Collections;
using System.IO;
using System.Text;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.InputSystem.Utilities;

/// <summary>Creates the whole game at runtime: world, car, camera, HUD. The scene itself stays empty.</summary>
public class GameBootstrap : MonoBehaviour
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Boot()
    {
        Log.Init();
        var go = new GameObject("Game");
        go.AddComponent<GameBootstrap>();
        DontDestroyOnLoad(go);
    }

    WorldBuilder world; CarController car; FollowCamera cam; MapView map; Camera mainCam; CarAudio audio; SpeedFx fx; TyreFx tyres;
    bool smoke; float smokeT, smokeSeconds = 24f;
    float fps, fpsAcc, fpsMin = 999f; int fpsN; float fpsNext, telemetryNext;
    GUIStyle big, small, mono;
    float lastInputErr; bool inputTest, mapTest, shotsMode; Vector3? shotFocus;
    bool showHelp = true, showDebug; Autopilot auto; bool autoOn;
    string padName = "none"; float steerIn, thrIn, brkIn; bool handIn;

    IEnumerator Start()
    {
        Application.targetFrameRate = -1; Application.runInBackground = true;
        QualitySettings.vSyncCount = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-novsync") >= 0 || System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-batchmode") >= 0 ? 0 : 1;
        Time.fixedDeltaTime = 0.01f;
        smoke = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-smoke") >= 0;
        inputTest = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-inputtest") >= 0;
        mapTest = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-maptest") >= 0;
        shotsMode = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-shots") >= 0;
        var args = System.Environment.GetCommandLineArgs();
        int si = System.Array.IndexOf(args, "-smokeSeconds"); if (si >= 0 && si + 1 < args.Length) float.TryParse(args[si + 1], out smokeSeconds);
        LogSystem();
        SetupInput();

        var sky = new Color(0.62f, 0.78f, 0.93f);
        RenderSettings.fog = true; RenderSettings.fogMode = FogMode.Linear; RenderSettings.fogColor = sky;
        RenderSettings.fogStartDistance = 500f; RenderSettings.fogEndDistance = 2800f;

        var camGo = new GameObject("Main Camera") { tag = "MainCamera" };
        var camera = camGo.AddComponent<Camera>();
        camera.clearFlags = CameraClearFlags.SolidColor; camera.backgroundColor = sky;
        camera.nearClipPlane = 0.3f; camera.farClipPlane = 3400f;
        camGo.AddComponent<AudioListener>();
        cam = camGo.AddComponent<FollowCamera>(); mainCam = camera;

        world = new GameObject("World").AddComponent<WorldBuilder>();
        Log.I("boot", "building world…");
        yield return world.Build();
        if (world.Error != null) { Log.I("boot", "world failed: " + world.Error); yield break; }

        var sp = world.Data.Spawn;
        float gy = world.GroundHeight(sp.x, sp.z, sp.y, out _);
        var carGo = new GameObject("Car");
        carGo.transform.SetPositionAndRotation(new Vector3(sp.x, gy + 0.9f, sp.z), Quaternion.Euler(0, sp.heading, 0));   // pose first, THEN the Rigidbody
        carGo.AddComponent<Rigidbody>();
        var mat = new Material(GameAssets.Flat);
        var wheels = CarVisual.Build(carGo.transform, mat);
        car = carGo.AddComponent<CarController>();
        car.Init(world, wheels);
        cam.Car = car; cam.Snap();
        map = gameObject.AddComponent<MapView>(); map.Init(world, car, cam, mainCam);
        audio = carGo.AddComponent<CarAudio>(); audio.Car = car;
        fx = mainCam.gameObject.AddComponent<SpeedFx>(); fx.Car = car;
        tyres = carGo.AddComponent<TyreFx>(); tyres.Init(car);
        Log.I("boot", $"car spawned at ({sp.x:F1}, {gy:F1}, {sp.z:F1}) heading {sp.heading:F0}° smoke={smoke}");
        var launchArgs = System.Environment.GetCommandLineArgs();
        if (System.Array.IndexOf(launchArgs, "-autopilot") >= 0)
        {
            auto = new Autopilot(world.Data, car); autoOn = true; auto.TargetKmh = 95f;
            int ai2 = System.Array.IndexOf(launchArgs, "-autoKmh"); if (ai2 >= 0 && ai2 + 1 < launchArgs.Length && float.TryParse(launchArgs[ai2 + 1], out float ak2)) auto.TargetKmh = ak2;
            Log.I("auto", $"autopilot demo started at {auto.TargetKmh:F0} km/h target (P / Circle to take over)");
        }
        if (smoke) StartCoroutine(SmokeTest());
        if (inputTest) StartCoroutine(InputTest());
        if (mapTest) StartCoroutine(MapTest());
        if (shotsMode) StartCoroutine(Shots());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-camtest") >= 0) StartCoroutine(CamTest());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-smokeshots") >= 0) StartCoroutine(SmokeShots());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-audiotest") >= 0) StartCoroutine(AudioTest());
    }

    // ------------------------------------------------------------ logging helpers
    void LogSystem()
    {
        Log.I("sys", $"Unity {Application.unityVersion} on {SystemInfo.operatingSystem}");
        Log.I("sys", $"GPU {SystemInfo.graphicsDeviceName} ({SystemInfo.graphicsDeviceType}) VRAM={SystemInfo.graphicsMemorySize} MB, CPU {SystemInfo.processorType} x{SystemInfo.processorCount}, RAM {SystemInfo.systemMemorySize} MB");
        Log.I("sys", $"screen {Screen.width}x{Screen.height} fullscreen={Screen.fullScreenMode} refresh={Screen.currentResolution.refreshRateRatio.value:F0} Hz, log file={Log.Path_}");
        Log.I("sys", $"streamingAssets={Application.streamingAssetsPath} exists={Directory.Exists(Path.Combine(Application.streamingAssetsPath, "berat"))}");
    }

    void SetupInput()
    {
        try { InputSystem.settings.backgroundBehavior = InputSettings.BackgroundBehavior.IgnoreFocus; } catch (System.Exception e) { Log.I("input", "backgroundBehavior: " + e.Message); }
        foreach (var d in InputSystem.devices) Log.I("input", $"device present: '{d.displayName}' layout={d.layout} iface={d.description.interfaceName} product={d.description.product} vendor={d.description.manufacturer}");
        InputSystem.onDeviceChange += (d, change) =>
        {
            Log.I("input", $"device {change}: '{d.displayName}' layout={d.layout} iface={d.description.interfaceName}");
            RefreshPad();
        };
        InputSystem.onAnyButtonPress.Call(ctrl => Log.I("input", $"button event: {ctrl.device.displayName} / {ctrl.path}"));
        RefreshPad();
    }

    void OnApplicationFocus(bool f) { Log.I("sys", "focus " + (f ? "GAINED" : "LOST")); }

    /// <summary>Pick the controller: current Gamepad, else any connected gamepad (DualSense/DualShock/Xbox layouts all derive from Gamepad).</summary>
    Gamepad pad;
    void RefreshPad()
    {
        var g = Gamepad.current;
        if (inputTest && Gamepad.all.Count > 0) g = Gamepad.all[Gamepad.all.Count - 1];   // the synthetic pad
        if (g == null && Gamepad.all.Count > 0) g = Gamepad.all[0];
        if (g != pad)
        {
            pad = g;
            padName = pad != null ? $"{pad.displayName} ({pad.layout})" : "none";
            Log.I("input", pad != null ? $"CONTROLLER ACTIVE: {padName}" : "no controller: keyboard only");
        }
    }

    // ------------------------------------------------------------ frame
    void Update()
    {
        if (world == null || !world.Ready || car == null) return;
        var kbM = Keyboard.current; var gpM = Gamepad.current;
        if (!smoke && ((kbM != null && kbM.mKey.wasPressedThisFrame) || (gpM != null && gpM.selectButton.wasPressedThisFrame))) { map.Toggle(); }
        world.UpdateStreaming(shotFocus.HasValue ? shotFocus.Value : (map.Active ? map.Focus : car.transform.position));
        if (map.Active) { map.Tick(); return; }
        HandleCameraInput();
        fpsAcc += Time.unscaledDeltaTime; fpsN++; fpsMin = Mathf.Min(fpsMin, 1f / Mathf.Max(Time.unscaledDeltaTime, 1e-4f));
        if (Time.unscaledTime > fpsNext) { fps = fpsN / fpsAcc; fpsAcc = 0; fpsN = 0; fpsNext = Time.unscaledTime + 0.5f; }
        if (autoOn && auto != null) { auto.Drive(Time.deltaTime); if (Time.unscaledTime > telemetryNext) { Log.I("auto", auto.Status); Telemetry(); } if (!smoke) HandleToggles(); return; }
        if (smoke) return;

        float steer = 0, thr = 0, brk = 0; bool hand = false, reset = false;
        try {
        RefreshPad();
        var gp = pad; var kb = Keyboard.current;
        if (gp != null)
        {
            Vector2 st = gp.leftStick.ReadValue();
            steer += Curve(Deadzone(st.x, 0.08f));
            thr = Mathf.Max(thr, gp.rightTrigger.ReadValue()); brk = Mathf.Max(brk, gp.leftTrigger.ReadValue());
            hand |= gp.buttonWest.isPressed || gp.rightShoulder.isPressed;        // Square / R1
            if (gp.buttonEast.wasPressedThisFrame) ToggleAuto();                  // Circle
            reset |= gp.buttonNorth.wasPressedThisFrame;                          // Triangle
            if (gp.startButton.wasPressedThisFrame) { showHelp = !showHelp; Log.I("input", "help toggled"); }
            foreach (var c in gp.allControls)                                     // log every button press so mapping issues are visible
                if (c is UnityEngine.InputSystem.Controls.ButtonControl b && b.wasPressedThisFrame && !(c.name.EndsWith("Trigger") || c.name.StartsWith("dpad") && false))
                    Log.I("input", $"pad button '{c.name}' pressed");
        }
        if (kb != null)
        {
            if (kb.pKey.wasPressedThisFrame) { ToggleAuto(); }
            steer += (kb.dKey.isPressed || kb.rightArrowKey.isPressed ? 1 : 0) - (kb.aKey.isPressed || kb.leftArrowKey.isPressed ? 1 : 0);
            thr = Mathf.Max(thr, kb.wKey.isPressed || kb.upArrowKey.isPressed ? 1 : 0);
            brk = Mathf.Max(brk, kb.sKey.isPressed || kb.downArrowKey.isPressed ? 1 : 0);
            hand |= kb.spaceKey.isPressed; reset |= kb.rKey.wasPressedThisFrame;
            if (kb.hKey.wasPressedThisFrame) showHelp = !showHelp;
            if (audio != null && (kb.leftBracketKey.wasPressedThisFrame || kb.rightBracketKey.wasPressedThisFrame))
            { audio.Volume = Mathf.Clamp(audio.Volume + (kb.rightBracketKey.wasPressedThisFrame ? 0.05f : -0.05f), 0f, 1f); Log.I("audio", $"volume {audio.Volume * 100:F0}%"); }
            if (kb.nKey.wasPressedThisFrame && audio != null) { audio.Synth.Muted = !audio.Synth.Muted; Log.I("audio", "muted=" + audio.Synth.Muted); }
            if (kb.f3Key.wasPressedThisFrame) showDebug = !showDebug;
            if (kb.escapeKey.wasPressedThisFrame) { Log.I("boot", "quit (Esc)"); Application.Quit(); }
        }
        steerIn = Mathf.Clamp(steer, -1, 1); thrIn = thr; brkIn = brk; handIn = hand;
        car.Steer = steerIn; car.Throttle = thrIn; car.Brake = brkIn; car.Handbrake = handIn;
        } catch (System.Exception e) { if (Time.unscaledTime > lastInputErr) { lastInputErr = Time.unscaledTime + 2f; Log.I("input", "INPUT EXCEPTION: " + e); } }
        if (reset)
        {
            var p = car.transform.position;
            car.Respawn(new Vector3(p.x, world.GroundHeight(p.x, p.z, p.y, out _) + 1f, p.z));
            cam.Snap();
        }
        if (Time.unscaledTime > telemetryNext) Telemetry();
    }

    void ToggleAuto()
    {
        autoOn = !autoOn; if (autoOn) auto = new Autopilot(world.Data, car);
        Log.I("auto", "autopilot " + (autoOn ? "ON" : "OFF"));
    }
    void HandleToggles()
    {
        var kb = Keyboard.current; var gp = pad;
        if ((kb != null && kb.pKey.wasPressedThisFrame) || (gp != null && gp.buttonEast.wasPressedThisFrame)) ToggleAuto();
        if (kb != null && kb.escapeKey.wasPressedThisFrame) Application.Quit();
    }

    /// <summary>C / D-pad up: next camera.  Right stick or hold right mouse: look around.  Hold B / R3: rear view.</summary>
    void HandleCameraInput()
    {
        var kb = Keyboard.current; var mouse = Mouse.current; var gp = pad;
        bool back = false;
        if (kb != null) { if (kb.cKey.wasPressedThisFrame) cam.NextMode(); back |= kb.bKey.isPressed; }
        if (mouse != null && mouse.rightButton.isPressed) cam.Look(mouse.delta.ReadValue() * 0.18f);
        if (gp != null)
        {
            if (gp.dpad.up.wasPressedThisFrame) cam.NextMode();
            Vector2 rs = gp.rightStick.ReadValue();
            if (rs.magnitude > 0.2f) cam.Look(rs * 170f * Time.unscaledDeltaTime);
            back |= gp.rightStickButton.isPressed;
        }
        cam.LookBack(back);
    }

    static float Deadzone(float v, float dz) => Mathf.Abs(v) < dz ? 0 : Mathf.Sign(v) * (Mathf.Abs(v) - dz) / (1 - dz);
    static float Curve(float v) => Mathf.Sign(v) * (0.35f * Mathf.Abs(v) + 0.65f * v * v);   // finer control near centre

    void Telemetry()
    {
        telemetryNext = Time.unscaledTime + 1f;
        var p = car.transform.position;
        Log.I("tel", $"pos=({p.x:F0},{p.y:F1},{p.z:F0}) {car.SpeedKmh:F0}km/h fwd={car.ForwardSpeed:F1}m/s wheels={car.WheelsOnGround}/4 surf={car.CurrentSurface} " +
                     $"in[steer={steerIn:F2} thr={thrIn:F2} brk={brkIn:F2} hand={handIn}] gear={(audio != null ? audio.Gear : 0)} rpm={(audio != null ? audio.Rpm : 0):F0} audioPeak={(audio != null ? audio.Synth.LastPeak : 0):F2} buffers={(audio != null ? audio.Synth.Buffers : 0)} fx={(fx != null ? fx.Strength : 0):F2} smoke={(tyres != null ? tyres.Alive : 0)} wfx=[{car.WheelFx[0]:F1},{car.WheelFx[1]:F1},{car.WheelFx[2]:F1},{car.WheelFx[3]:F1}] cam={(cam != null ? cam.ModeName : "-")} fov={(mainCam != null ? mainCam.fieldOfView : 0):F0} pad={padName} focus={Application.isFocused} kb={(Keyboard.current != null)} auto={autoOn} fps={fps:F0} (min {fpsMin:F0}) mem={System.GC.GetTotalMemory(false) / 1048576}MB");
        fpsMin = 999f;
    }

    /// <summary>Injects synthetic keyboard, then gamepad, state through the Input System and checks the car responds.</summary>
    IEnumerator InputTest()
    {
        yield return new WaitForSeconds(1.5f);
        var kb = Keyboard.current ?? InputSystem.AddDevice<Keyboard>();
        Log.I("itest", $"phase 1: keyboard W held (device {kb.displayName})");
        float x0 = car.transform.position.magnitude;
        for (float t = 0; t < 3f; t += Time.deltaTime)
        {
            InputSystem.QueueStateEvent(kb, new KeyboardState(Key.W));
            yield return null;
        }
        Log.I("itest", $"keyboard result: speed={car.SpeedKmh:F0} km/h throttle={thrIn:F2} steer={steerIn:F2} -> {(car.SpeedKmh > 8f && thrIn > 0.9f ? "PASS" : "FAIL")}");
        InputSystem.QueueStateEvent(kb, new KeyboardState());
        yield return new WaitForSeconds(2.5f);
        var gp = InputSystem.AddDevice<Gamepad>();
        Log.I("itest", $"phase 2: gamepad RT + left stick (device {gp.displayName})");
        for (float t = 0; t < 3f; t += Time.deltaTime)
        {
            InputSystem.QueueStateEvent(gp, new GamepadState { rightTrigger = 1f, leftStick = new Vector2(0.6f, 0f) });
            yield return null;
        }
        Log.I("itest", $"gamepad result: speed={car.SpeedKmh:F0} km/h throttle={thrIn:F2} steer={steerIn:F2} -> {(thrIn > 0.9f && steerIn > 0.1f ? "PASS" : "FAIL")}");
        InputSystem.QueueStateEvent(gp, new GamepadState { leftTrigger = 1f });
        yield return new WaitForSeconds(1.5f);
        Log.I("itest", $"brake: brake={brkIn:F2} speed={car.SpeedKmh:F0} km/h");
        Log.I("itest", "done");
        Application.Quit();
    }

    IEnumerator Hold(Keyboard kb, Key key, float seconds)
    {
        for (float t = 0; t < seconds; t += Time.unscaledDeltaTime) { InputSystem.QueueStateEvent(kb, new KeyboardState(key)); yield return null; }
        InputSystem.QueueStateEvent(kb, new KeyboardState()); yield return null; yield return null;
    }

    /// <summary>Drives the real MapView code through injected keys: pan, zoom levels, border clamp, teleport.</summary>
    IEnumerator MapTest()
    {
        var kb = Keyboard.current ?? InputSystem.AddDevice<Keyboard>();
        yield return new WaitForSecondsRealtime(1.5f);
        int fails = 0;
        void Check(string what, bool ok, string detail) { if (!ok) fails++; Log.I("mtest", $"{(ok ? "PASS" : "FAIL")}  {what}: {detail}"); }

        InputSystem.QueueStateEvent(kb, new KeyboardState(Key.M)); yield return null; InputSystem.QueueStateEvent(kb, new KeyboardState()); yield return null; yield return null;
        Check("open map", map.Active, $"alt={map.Altitude:F0} m level={map.Level + 1} pos={map.Pos}");
        Check("start altitude ~100 m", Mathf.Abs(map.Altitude - 100f) < 1f, $"{map.Altitude:F1}");

        Vector2 p0 = map.Pos;
        yield return Hold(kb, Key.D, 1f);
        Check("pan east", map.Pos.x > p0.x + 50f, $"x {p0.x:F0} -> {map.Pos.x:F0}");

        float[] expect = { 46.4159f, 21.5443f, 10f, 10f };
        for (int i = 0; i < expect.Length; i++)
        {
            yield return Hold(kb, Key.E, 0.05f); yield return new WaitForSecondsRealtime(0.6f);
            Check($"zoom in #{i + 1}", Mathf.Abs(map.Altitude - expect[i]) < expect[i] * 0.03f, $"alt {map.Altitude:F1} (want {expect[i]:F1}, min is 10)");
        }
        for (int i = 0; i < 8; i++) { yield return Hold(kb, Key.Q, 0.05f); yield return new WaitForSecondsRealtime(0.2f); }
        yield return new WaitForSecondsRealtime(0.8f);
        Check("zoom out clamps at 1 km", Mathf.Abs(map.Altitude - 1000f) < 30f, $"alt {map.Altitude:F0}");
        yield return Hold(kb, Key.E, 0.05f); yield return Hold(kb, Key.E, 0.05f); yield return Hold(kb, Key.E, 0.05f); yield return new WaitForSecondsRealtime(0.8f);
        Check("zoom back to 100 m", Mathf.Abs(map.Altitude - 100f) < 4f, $"alt {map.Altitude:F0}");

        map.DebugSetPos(3190f, 0f);
        yield return Hold(kb, Key.D, 0.8f);
        Check("border clamp east", map.Pos.x <= WorldData.Half - 5f && map.Pos.x > 3150f, $"x={map.Pos.x:F1} (limit {WorldData.Half - 6f:F0})");
        map.DebugSetPos(0f, -3190f);
        yield return Hold(kb, Key.S, 0.8f);
        Check("border clamp south", map.Pos.y >= -(WorldData.Half - 5f) && map.Pos.y < -3150f, $"z={map.Pos.y:F1}");

        map.DebugSetPos(700f, 400f);
        yield return null; yield return null;
        var aim = map.Aim; bool valid = map.AimValid;
        float terr = world.Data.TerrainHeight(700f, 400f);
        Check("aim under camera", valid && Vector2.Distance(new Vector2(aim.x, aim.z), new Vector2(700f, 400f)) < 3f && Mathf.Abs(aim.y - terr) < 0.05f, $"aim=({aim.x:F1},{aim.y:F2},{aim.z:F1}) terrain={terr:F2}");
        InputSystem.QueueStateEvent(kb, new KeyboardState(Key.Enter)); yield return null; InputSystem.QueueStateEvent(kb, new KeyboardState()); yield return null; yield return null;
        Check("map closed after teleport", !map.Active, $"active={map.Active} timeScale={Time.timeScale}");
        var cp = car.transform.position;
        Check("car at target", Vector2.Distance(new Vector2(cp.x, cp.z), new Vector2(aim.x, aim.z)) < 1.5f, $"car=({cp.x:F1},{cp.y:F2},{cp.z:F1}) target=({aim.x:F1},{aim.z:F1})");
        yield return new WaitForSeconds(3f);
        cp = car.transform.position; float gy = world.GroundHeight(cp.x, cp.z, cp.y, out _);
        Check("car settled on ground", car.WheelsOnGround == 4 && cp.y - gy > 0.2f && cp.y - gy < 1.2f, $"wheels={car.WheelsOnGround}/4 car.y={cp.y:F2} ground={gy:F2}");
        Log.I("mtest", fails == 0 ? "ALL PASS" : $"{fails} FAILED");
        Application.Quit();
    }

    /// <summary>Headless screenshots (render to texture, no window): one view per landmark + a few generic ones.</summary>
    IEnumerator Shots()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "docs", "shots")); Directory.CreateDirectory(dir);
        var camera = mainCam; cam.enabled = false; map.enabled = false;
        var rt = new RenderTexture(1600, 900, 24); camera.targetTexture = rt; camera.fieldOfView = 55f;
        var tex = new Texture2D(1600, 900, TextureFormat.RGB24, false);
        var targets = new System.Collections.Generic.List<(string name, BuildingData b)>();
        foreach (var kind in new[] { "church", "chapel", "townhall", "pharmacy", "grocery", "bakery", "school", "hall", "restaurant", "bar", "post", "barn", "industrial" })
            foreach (var b in world.Data.Buildings) if (b.k == kind && !string.IsNullOrEmpty(b.n)) { targets.Add((kind + "_" + b.n, b)); break; }
        int houses = 0;
        foreach (var b in world.Data.Buildings) { if (b.k == "house" && b.p.Length > 12) { targets.Add(("house" + houses, b)); if (++houses >= 4) break; } }
        foreach (var t in targets)
        {
            var b = t.b; int n = b.p.Length / 2; Vector2 cen = Vector2.zero;
            for (int i = 0; i < n; i++) cen += new Vector2(b.p[i * 2], b.p[i * 2 + 1]); cen /= n;
            int fe = Mathf.Clamp(b.fe, 0, n - 1);
            Vector2 a = new Vector2(b.p[fe * 2], b.p[fe * 2 + 1]), c2 = new Vector2(b.p[((fe + 1) % n) * 2], b.p[((fe + 1) % n) * 2 + 1]);
            Vector2 mid = (a + c2) / 2, d = (c2 - a).normalized, nrm = new Vector2(d.y, -d.x);
            if (Vector2.Dot(nrm, mid - cen) < 0) nrm = -nrm;
            float size = Mathf.Max(8f, (a - c2).magnitude + 6f);
            Vector3 look = new Vector3(cen.x * 0.35f + mid.x * 0.65f, b.b + 1f + Mathf.Min(b.h * 0.55f, 7f), cen.y * 0.35f + mid.y * 0.65f);
            Vector3 best = default; bool found = false;
            Vector3 aimPt = new Vector3(mid.x + nrm.x * 1.2f, b.b + 3f, mid.y + nrm.y * 1.2f);      // just in front of the facade
            shotFocus = new Vector3(mid.x, 0, mid.y); nextForce(); world.UpdateStreaming(shotFocus.Value);
            yield return null; yield return null; Physics.SyncTransforms();
            foreach (float dm in new[] { 1.4f, 1.9f, 1.1f, 2.4f })
                foreach (float sideOff in new[] { 4f, -4f, 10f, -10f, 0f })
                {
                    float dd = Mathf.Clamp(size * dm, 20f, 60f);
                    Vector2 cxz = mid + nrm * dd + d * sideOff;
                    float g = world.Data.TerrainHeight(cxz.x, cxz.y);
                    Vector3 cp = new Vector3(cxz.x, g + 6.5f, cxz.y);
                    if (Physics.CheckSphere(cp, 1.5f)) continue;                                        // inside a building / tree collider
                    if (Physics.Linecast(cp, aimPt)) continue;                                          // something in the way
                    best = cp; found = true; break;
                }
            if (!found) { best = new Vector3(mid.x + nrm.x * 40f, world.Data.TerrainHeight(mid.x, mid.y) + 14f, mid.y + nrm.y * 40f); Log.I("shots", "no clear view for " + t.name + ", using fallback"); }
            float dist = (best - aimPt).magnitude;
            camera.transform.position = best;
            camera.transform.LookAt(look);
            shotFocus = camera.transform.position; nextForce();
            yield return null; yield return null; yield return new WaitForSecondsRealtime(0.3f);
            camera.Render();
            RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0); tex.Apply(); RenderTexture.active = null;
            string file = Path.Combine(dir, Sanitize(t.name) + ".png"); File.WriteAllBytes(file, tex.EncodeToPNG());
            Log.I("shots", $"cam=({camera.transform.position.x:F0},{camera.transform.position.y:F0},{camera.transform.position.z:F0}) target=({cen.x:F0},{cen.y:F0}) dist={dist:F0} wallH={b.h:F1} rise={b.r:F1} edge={(a - c2).magnitude:F1}m");
            Log.I("shots", $"{t.name}  kind={b.k} floors≈{Mathf.RoundToInt((b.h - 1.3f) / 3f)} -> {file}");
        }
        // extra in-game style views: chase camera at spawn, a village overview, and a low road-level view
        var sp2 = world.Data.Spawn; Vector3 carPos = car.transform.position;
        var extra = new (string name, Vector3 pos, Vector3 look)[] {
            ("view_chase_spawn", carPos - car.transform.forward * 5.3f + Vector3.up * 2.4f, carPos + Vector3.up * 1.1f + car.transform.forward * 3f),
            ("view_village_overview", new Vector3(sp2.x - 120f, sp2.y + 110f, sp2.z - 160f), new Vector3(sp2.x + 30f, sp2.y, sp2.z + 60f)),
            ("view_road_level", carPos + Vector3.up * 1.2f - car.transform.forward * 1f, carPos + car.transform.forward * 40f + Vector3.up * 1.5f),
            ("view_church_far", new Vector3(sp2.x - 60f, sp2.y + 12f, sp2.z - 90f), new Vector3(72f, sp2.y + 12f, 250f)) };
        foreach (var v in extra)
        {
            camera.transform.position = v.pos; camera.transform.LookAt(v.look);
            shotFocus = v.pos; nextForce(); yield return null; yield return null; yield return new WaitForSecondsRealtime(0.3f);
            camera.Render();
            RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0); tex.Apply(); RenderTexture.active = null;
            File.WriteAllBytes(Path.Combine(dir, v.name + ".png"), tex.EncodeToPNG());
            Log.I("shots", v.name);
        }
        Log.I("shots", "done");
        Application.Quit();
    }
    void nextForce() { world.ForceStream(); }
    static string Sanitize(string s) { foreach (char ch in Path.GetInvalidFileNameChars()) s = s.Replace(ch, '_'); return s.Replace(' ', '_').Replace('\'', '_'); }

    /// <summary>Renders the synth offline to WAV files for a set of driving situations and logs peak/NaN health.</summary>
    IEnumerator AudioTest()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "docs", "audio")); Directory.CreateDirectory(dir);
        var cases = new (string name, float rpm, float load, float speed, float slip, float rough)[] {
            ("idle", 900, 0.1f, 0, 0, 1), ("cruise_50kmh", 2600, 0.3f, 14, 0, 1), ("accel_120kmh", 5200, 1f, 33, 0, 1),
            ("redline_240kmh", 7000, 1f, 67, 0, 1), ("coast_wind_200kmh", 1500, 0f, 56, 0, 1), ("drift_squeal_70kmh", 4200, 0.9f, 19, 1f, 1), ("dirt_60kmh", 3200, 0.5f, 17, 0.2f, 2.2f) };
        foreach (var c in cases)
        {
            var syn = new CarSynth { Rpm = c.rpm, Load = c.load, SpeedMs = c.speed, Slip = c.slip, Rough = c.rough, Master = 0.7f };
            const int sr = 48000; int frames = sr * 3; var buf = new float[frames * 2]; var pcm = new short[frames];
            for (int off = 0; off < frames; off += 1024)
            {
                int n = Mathf.Min(1024, frames - off); var chunk = new float[n * 2]; syn.Fill(chunk, 2, sr);
                for (int i = 0; i < n; i++) pcm[off + i] = (short)(Mathf.Clamp(chunk[i * 2], -1f, 1f) * 32000f);
            }
            WriteWav(Path.Combine(dir, c.name + ".wav"), pcm, sr);
            Log.I("audiotest", $"{c.name}: peak={syn.LastPeak:F2} nan={syn.NaNSeen}");
            yield return null;
        }
        Log.I("audiotest", "done -> " + dir);
        Application.Quit();
    }

    static void WriteWav(string path, short[] pcm, int sr)
    {
        using (var bw = new BinaryWriter(File.Create(path)))
        {
            bw.Write(System.Text.Encoding.ASCII.GetBytes("RIFF")); bw.Write(36 + pcm.Length * 2); bw.Write(System.Text.Encoding.ASCII.GetBytes("WAVEfmt "));
            bw.Write(16); bw.Write((short)1); bw.Write((short)1); bw.Write(sr); bw.Write(sr * 2); bw.Write((short)2); bw.Write((short)16);
            bw.Write(System.Text.Encoding.ASCII.GetBytes("data")); bw.Write(pcm.Length * 2);
            foreach (var v in pcm) bw.Write(v);
        }
    }

    /// <summary>Headless: burnout + drift on the road, render the chase cam to PNGs to check tyre smoke and skid marks.</summary>
    /// <summary>Orbits the free-look camera around the parked car and checks the car stays at the centre of the view.</summary>
    IEnumerator CamTest()
    {
        yield return new WaitForSeconds(1.5f);
        int fails = 0;
        foreach (var mode in new[] { CamMode.Chase, CamMode.Close, CamMode.Far })
        {
            while (cam.Mode != mode) cam.NextMode();
            foreach (var (yaw, pitch, back, name) in new[] { (0f, 0f, false, "default"), (45f, 0f, false, "yaw 45"), (90f, 0f, false, "yaw 90"), (-90f, 20f, false, "yaw -90 pitch 20"), (150f, 0f, false, "yaw 150"), (0f, 40f, false, "pitch 40"), (180f, 0f, false, "rear view (180)") })
            {
                cam.Look(new Vector2(-9999, 0)); cam.Look(new Vector2(0, 9999));      // reset offsets to a known state, then set the wanted ones
                cam.SetFreeLook(yaw, pitch); cam.LookBack(back);
                for (int i = 0; i < 90; i++) { cam.SetFreeLook(yaw, pitch); yield return null; }
                float err = cam.AimErrorDeg; bool ok = name == "default" || err < 2.5f; if (!ok) fails++;
                Log.I("camtest", $"{(ok ? "PASS" : "FAIL")}  {mode,-6} {name,-18} aim error {err:F1} deg from car centre  (distance {Vector3.Distance(cam.transform.position, car.transform.position):F1} m)");
            }
        }
        cam.LookBack(false);
        Log.I("camtest", fails == 0 ? "ALL PASS" : fails + " FAILED"); Application.Quit();
    }

    IEnumerator SmokeShots()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "docs", "shots")); Directory.CreateDirectory(dir);
        var rt = new RenderTexture(1600, 900, 24); mainCam.targetTexture = rt; var tex = new Texture2D(1600, 900, TextureFormat.RGB24, false);
        yield return new WaitForSeconds(1f);
        // shot A: standing burnout (throttle + handbrake); shot B: hard drift at speed
        float t0 = Time.time; int n = 0;
        while (Time.time - t0 < 6.5f)
        {
            float t = Time.time - t0;
            car.Throttle = 1f; car.Brake = 0f;
            if (t < 2.6f) { car.Handbrake = true; car.Steer = 0f; }                                     // burnout: rear tyres lose grip
            else if (t < 3.6f) { car.Handbrake = false; car.Steer = 0f; }
            else { car.Handbrake = t > 4.6f && t < 5.4f; car.Steer = t > 4.2f ? 0.9f : 0f; }              // then a drift turn
            if ((Mathf.Abs(t - 1.6f) < 0.011f || Mathf.Abs(t - 2.4f) < 0.011f || Mathf.Abs(t - 5.0f) < 0.011f || Mathf.Abs(t - 5.8f) < 0.011f))
            {
                mainCam.Render();                                                   // headless: no window loop, so render explicitly
                RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0); tex.Apply(); RenderTexture.active = null;
                File.WriteAllBytes(Path.Combine(dir, $"tyre_smoke_{n++}.png"), tex.EncodeToPNG());
                Log.I("smokeshots", $"t={t:F1} speed={car.SpeedKmh:F0} km/h particles={tyres.Alive} wfx=[{car.WheelFx[0]:F2},{car.WheelFx[1]:F2},{car.WheelFx[2]:F2},{car.WheelFx[3]:F2}]");
            }
            yield return null;
        }
        Log.I("smokeshots", "done"); Application.Quit();
    }

    IEnumerator SmokeTest()
    {
        string dir = Path.Combine(Application.dataPath, "..", "..", "smoke"); Directory.CreateDirectory(dir);
        Log.I("smoke", "scripted drive start; screenshots -> " + Path.GetFullPath(dir));
        for (smokeT = 0; smokeT < smokeSeconds; smokeT += Time.deltaTime)
        {
            if (!autoOn && smokeT > 1f) { auto = new Autopilot(world.Data, car); autoOn = true; var a2 = System.Environment.GetCommandLineArgs(); int ai = System.Array.IndexOf(a2, "-autoKmh"); if (ai >= 0 && ai + 1 < a2.Length && float.TryParse(a2[ai + 1], out float ak)) auto.TargetKmh = ak; }
            steerIn = car.Steer; thrIn = car.Throttle;
            foreach (float t in new[] { 4f, 12f, 20f })
                if (smokeT >= t && smokeT - Time.deltaTime < t) ScreenCapture.CaptureScreenshot(Path.Combine(dir, $"shot_{(int)t}.png"));
            if (Time.unscaledTime > telemetryNext) Telemetry();
            yield return null;
        }
        Log.I("smoke", "done");
        Application.Quit();
    }

    // ------------------------------------------------------------ HUD
    static void Bar(Rect r, float v, Color fill, string label, GUIStyle st)
    {
        GUI.color = new Color(0, 0, 0, 0.55f); GUI.DrawTexture(r, Texture2D.whiteTexture);
        GUI.color = fill; GUI.DrawTexture(new Rect(r.x + 2, r.yMax - 2 - (r.height - 4) * Mathf.Clamp01(v), r.width - 4, (r.height - 4) * Mathf.Clamp01(v)), Texture2D.whiteTexture);
        GUI.color = Color.white; GUI.Label(new Rect(r.x - 10, r.yMax + 1, r.width + 20, 20), label, st);
    }
    void DrawGauges()
    {
        var lbl = new GUIStyle(small) { alignment = TextAnchor.UpperCenter, fontSize = 12 };
        float x = Screen.width - 460, y = Screen.height - 150;
        Bar(new Rect(x, y, 26, 110), car.Throttle, new Color(0.25f, 0.85f, 0.35f), "GAS", lbl);
        Bar(new Rect(x + 40, y, 26, 110), car.Brake, new Color(0.95f, 0.25f, 0.2f), "BRAKE", lbl);
        // steering: horizontal centre-zero bar
        var sr = new Rect(x - 130, y + 90, 110, 18);
        GUI.color = new Color(0, 0, 0, 0.55f); GUI.DrawTexture(sr, Texture2D.whiteTexture);
        GUI.color = new Color(0.3f, 0.7f, 1f); float w = car.Steer * (sr.width / 2 - 2);
        GUI.DrawTexture(new Rect(sr.center.x + Mathf.Min(0, w), sr.y + 2, Mathf.Abs(w), sr.height - 4), Texture2D.whiteTexture);
        GUI.color = Color.white; GUI.Label(new Rect(sr.x, sr.yMax + 1, sr.width, 20), "STEER", lbl);
        if (car.Handbrake) { GUI.color = new Color(1f, 0.6f, 0.1f); GUI.Label(new Rect(x - 130, y + 60, 110, 24), "HANDBRAKE", lbl); GUI.color = Color.white; }
    }

    void OnGUI()
    {
        if (big == null)
        {
            big = new GUIStyle(GUI.skin.label) { fontSize = 44, fontStyle = FontStyle.Bold, alignment = TextAnchor.LowerRight }; big.normal.textColor = Color.white;
            small = new GUIStyle(GUI.skin.label) { fontSize = 16 }; small.normal.textColor = Color.white;
            mono = new GUIStyle(small) { font = Font.CreateDynamicFontFromOSFont("Menlo", 14), fontSize = 13 };
        }
        if (world != null && world.Error != null)
        {
            GUI.Label(new Rect(20, 20, Screen.width - 40, 200), "Load failed: " + world.Error + "\nSee " + Log.Path_, small);
            return;
        }
        if (world == null || !world.Ready)
        {
            GUI.Label(new Rect(20, Screen.height - 60, 700, 40), $"Loading Berat…  {(world != null ? world.Progress * 100 : 0):F0}%", big);
            return;
        }
        if (map != null && map.Active) { map.DrawGUI(); return; }
        GUI.Label(new Rect(Screen.width - 340, Screen.height - 110, 320, 80), $"{car.SpeedKmh:F0} km/h", big);
        float camAge = Time.unscaledTime - cam.ModeShownAt;
        if (camAge < 2.2f) { var cs = new GUIStyle(big) { fontSize = 26, alignment = TextAnchor.LowerCenter }; cs.normal.textColor = new Color(1, 1, 1, Mathf.Clamp01(2.2f - camAge)); GUI.Label(new Rect(0, Screen.height - 90, Screen.width, 60), "Camera: " + cam.ModeName, cs); }
        DrawGauges();
        if (autoOn) { var ap = new GUIStyle(big) { fontSize = 30, alignment = TextAnchor.UpperCenter }; ap.normal.textColor = new Color(1f, 0.85f, 0.2f); GUI.Label(new Rect(0, 14, Screen.width, 44), "AUTOPILOT ON  (P / Circle to take over)", ap); }
        GUI.Label(new Rect(16, 12, 700, 24), $"{fps:F0} fps   |   pad: {padName}   |   gear {(audio != null ? audio.Gear : 0)}  {(audio != null ? audio.Rpm : 0):F0} rpm   vol {(audio != null ? audio.Volume * 100 : 0):F0}%{(audio != null && audio.Synth.Muted ? " [muted]" : "")}", small);
        if (showHelp)
            GUI.Label(new Rect(16, 36, 900, 90), "Berat (31370) — LiDAR HD + BD TOPO\nDrive: W/S A/D  or  R2 / L2 + left stick     Handbrake: Space / Square / R1     Reset: R / Triangle     Autopilot: P / Circle     MAP: M / Select     Camera: C / D-pad up   Look: right stick / right-drag   Rear: B / R3   Volume: [ ]   Mute: N\nHelp: H / Options     Debug: F3     Quit: Esc", small);
        if (showDebug)
        {
            var sb = new StringBuilder();
            var p = car.transform.position;
            sb.AppendLine($"pos {p.x:F0},{p.y:F1},{p.z:F0}   surf {car.CurrentSurface}   wheels {car.WheelsOnGround}/4");
            sb.AppendLine($"input steer {steerIn:F2} thr {thrIn:F2} brk {brkIn:F2} hand {handIn}");
            sb.AppendLine($"log: {Log.Path_}");
            GUI.Label(new Rect(16, Screen.height - 90, 900, 80), sb.ToString(), mono);
        }
    }
}
