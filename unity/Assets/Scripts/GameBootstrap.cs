using System.Collections;
using System.Collections.Generic;
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
    bool scriptedTest => System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-phystest") >= 0 || System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-roadtest") >= 0 || System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-bridgetest") >= 0 || System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-hulltest") >= 0 || System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-stabtest") >= 0 || System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-hitboxtest") >= 0;   // physics harness owns the car inputs
    bool showHelp = true, showDebug; Autopilot auto; bool autoOn;
    string padName = "none"; float steerIn, thrIn, brkIn; bool handIn;

    IEnumerator Start()
    {
        Application.targetFrameRate = -1; Application.runInBackground = true;
        QualitySettings.vSyncCount = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-vsync") >= 0 ? 1 : 0;      // uncapped by default; V toggles
        Time.fixedDeltaTime = 0.01f;
        smoke = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-smoke") >= 0;
        inputTest = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-inputtest") >= 0;
        mapTest = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-maptest") >= 0;
        shotsMode = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-shots") >= 0;
        var args = System.Environment.GetCommandLineArgs();
        int si = System.Array.IndexOf(args, "-smokeSeconds"); if (si >= 0 && si + 1 < args.Length) float.TryParse(args[si + 1], out smokeSeconds);
        LogSystem();
        SetupInput();

        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-mute") >= 0) { AudioListener.volume = 0f; Log.I("audio", "-mute: all sound off"); }
        SetupEnvironment();

        var camGo = new GameObject("Main Camera") { tag = "MainCamera" };
        var camera = camGo.AddComponent<Camera>();
        camera.clearFlags = CameraClearFlags.Skybox; camera.allowHDR = true; camera.allowMSAA = true;
        camera.nearClipPlane = 0.3f; camera.farClipPlane = 60000f;
        camGo.AddComponent<AudioListener>();
        cam = camGo.AddComponent<FollowCamera>(); mainCam = camera;

        world = new GameObject("World").AddComponent<WorldBuilder>();
        Log.I("boot", "building world…");
        yield return world.Build();
        if (world.Error != null) { Log.I("boot", "world failed: " + world.Error); yield break; }

        var sp = world.Data.Spawn;
        int startModel = 0; { var la = System.Environment.GetCommandLineArgs(); int ci = System.Array.IndexOf(la, "-car"); if (ci >= 0 && ci + 1 < la.Length) int.TryParse(la[ci + 1], out startModel); }
        map = gameObject.AddComponent<MapView>();
        SpawnCar(Mathf.Clamp(startModel, 0, CarSpec.All.Length - 1), new Vector3(sp.x, 0, sp.z), sp.heading, true);
        Log.I("boot", $"smoke={smoke}");
        var launchArgs = System.Environment.GetCommandLineArgs();
        { int gi = System.Array.IndexOf(launchArgs, "-goto"); if (gi >= 0 && gi + 1 < launchArgs.Length && Grid.TryParse(string.Join(" ", launchArgs, gi + 1, Mathf.Min(2, launchArgs.Length - gi - 1)), out float gx, out float gz)) { TeleportCar(gx, gz); } }
        if (System.Array.IndexOf(launchArgs, "-autopilot") >= 0)
        {
            auto = new Autopilot(world.Data, car); autoOn = true;
            int ai2 = System.Array.IndexOf(launchArgs, "-autoKmh"); if (ai2 >= 0 && ai2 + 1 < launchArgs.Length && float.TryParse(launchArgs[ai2 + 1], out float ak2)) { auto.TargetKmh = ak2; auto.ObeyLimits = false; }      // an explicit speed overrides the limits
            Log.I("auto", $"autopilot demo started at {auto.TargetKmh:F0} km/h target (P / Circle to take over)");
        }
        if (smoke) StartCoroutine(SmokeTest());
        if (inputTest) StartCoroutine(InputTest());
        if (mapTest) StartCoroutine(MapTest());
        if (shotsMode) StartCoroutine(Shots());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-worldshots") >= 0) StartCoroutine(WorldShots());
        { var la2 = System.Environment.GetCommandLineArgs(); int si2 = System.Array.IndexOf(la2, "-shotat"); if (si2 >= 0 && si2 + 2 < la2.Length && float.TryParse(la2[si2 + 1], out float sx2) && float.TryParse(la2[si2 + 2], out float sz2)) StartCoroutine(ShotAt(sx2, sz2)); }
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-autotest") >= 0) StartCoroutine(AutoTest());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-mapshots") >= 0) StartCoroutine(MapShots());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-trafficshots") >= 0) StartCoroutine(TrafficShots());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-minimapshot") >= 0) StartCoroutine(MinimapShot());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-sparkletest") >= 0) StartCoroutine(SparkleTest());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-bridgetest") >= 0) StartCoroutine(PhysTest.BridgeRun(world, car));
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-hulltest") >= 0) StartCoroutine(PhysTest.HullRun(world, car));
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-audit") >= 0) { world.AuditObstacles(new Vector2(1037.5f, 1062.5f)); Application.Quit(); }
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-gridtest") >= 0) { PhysTest.GridRun(); Application.Quit(); }
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-groundscan") >= 0) StartCoroutine(GroundScan());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-roadtest") >= 0) StartCoroutine(PhysTest.RoadRun(world, car, 150f, 100f));
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-carshots") >= 0) StartCoroutine(CarShots());
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-phystest") >= 0) StartCoroutine(PhysTest.Run(world, car));
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-stabtest") >= 0) StartCoroutine(PhysTest.StabRun(world, car));
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-hitboxtest") >= 0) StartCoroutine(PhysTest.HitboxRun(world, car));
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

    // ------------------------------------------------------------ lighting / atmosphere
    Light sun;
    /// <summary>Afternoon sun with cascaded soft shadows, gradient ambient, procedural sky and matching aerial haze. All values are linear.</summary>
    public const float FogDensity = 0.00013f;      // exp2: ~90% visibility at 3 km, 25% at 10 km, gone by 25 km
    void SetupEnvironment()
    {
        var go = new GameObject("Sun");
        sun = go.AddComponent<Light>(); sun.type = LightType.Directional;
        sun.transform.rotation = Quaternion.Euler(41f, 32f, 0f);                     // sun in the south-west, ~41 deg up
        sun.color = new Color(1.0f, 0.93f, 0.82f); sun.intensity = 1.35f;
        sun.shadows = LightShadows.Soft; sun.shadowStrength = 0.92f; sun.shadowBias = 0.05f; sun.shadowNormalBias = 0.45f; sun.shadowNearPlane = 0.4f;
        RenderSettings.sun = sun;
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = new Color(0.27f, 0.35f, 0.50f);
        RenderSettings.ambientEquatorColor = new Color(0.31f, 0.32f, 0.30f);
        RenderSettings.ambientGroundColor = new Color(0.17f, 0.15f, 0.12f);
        var skyShader = Resources.Load<Shader>("BeratSky");
        if (skyShader != null) RenderSettings.skybox = new Material(skyShader); else Log.I("gfx", "sky shader missing");
        var haze = new Color(0.42f, 0.62f, 0.90f);
        RenderSettings.fog = true; RenderSettings.fogMode = FogMode.ExponentialSquared; RenderSettings.fogColor = haze; RenderSettings.fogDensity = FogDensity;

        QualitySettings.antiAliasing = 4;
        QualitySettings.shadows = ShadowQuality.All; QualitySettings.shadowResolution = ShadowResolution.VeryHigh; QualitySettings.shadowProjection = ShadowProjection.StableFit;
        QualitySettings.shadowCascades = 4; QualitySettings.shadowDistance = 260f; QualitySettings.shadowCascade4Split = new Vector3(0.04f, 0.14f, 0.40f);
        QualitySettings.anisotropicFiltering = AnisotropicFiltering.ForceEnable;
        Log.I("gfx", $"environment: sun {sun.transform.eulerAngles}, MSAA {QualitySettings.antiAliasing}x, shadows {QualitySettings.shadowResolution} {QualitySettings.shadowCascades} cascades to {QualitySettings.shadowDistance} m");
    }

    // ------------------------------------------------------------ cars
    MiniMap minimap; Traffic traffic;
    int carIndex; CarVisualRefs carVisual; Material carMat;

    /// <summary>Creates (or replaces) the player's car at a ground position. Everything that hangs off the car is rebuilt with it.</summary>
    void SpawnCar(int index, Vector3 pos, float heading, bool first)
    {
        carIndex = index; var spec = CarSpec.All[index];
        float gy = world.GroundHeight(pos.x, pos.z, world.Data.TerrainHeight(pos.x, pos.z) + 1f, out _);
        if (car != null) Destroy(car.gameObject);
        var carGo = new GameObject("Car");
        carGo.transform.SetPositionAndRotation(new Vector3(pos.x, gy + spec.WheelRadiusSum + 0.25f, pos.z), Quaternion.Euler(0, heading, 0));   // pose first, THEN the Rigidbody
        carGo.AddComponent<Rigidbody>();
        if (carMat == null) carMat = new Material(GameAssets.Flat);
        carVisual = CarVisual.Build(carGo.transform, carMat, spec);
        car = carGo.AddComponent<CarController>();
        car.Init(world, carVisual.wheels, spec);
        car.Impact += v => rumbleImpact = Mathf.Max(rumbleImpact, Mathf.Clamp01(v / 10f));
        cam.SetCar(car);
        map.Init(world, car, cam, mainCam);
        if (traffic == null && System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-notraffic") < 0 && !scriptedTest && !smoke) traffic = gameObject.AddComponent<Traffic>();
        if (traffic != null) { traffic.Init(world, car); Autopilot.TrafficRef = traffic; }
        if (minimap == null) minimap = gameObject.AddComponent<MiniMap>();
        minimap.Init(world, car, map);
        audio = carGo.AddComponent<CarAudio>(); audio.Car = car;
        if (fx == null) fx = mainCam.gameObject.AddComponent<SpeedFx>();
        fx.Car = car;
        tyres = carGo.AddComponent<TyreFx>(); tyres.Init(car);
        Log.I("boot", $"car '{spec.Name}' ({spec.Layout}) at ({pos.x:F1}, {gy:F1}, {pos.z:F1}) heading {heading:F0}");
        if (!first) { shownCarAt = Time.unscaledTime; auto = null; autoOn = false; }
    }
    float shownCarAt = -10f;

    void CycleCar()
    {
        var p = car.transform.position; float yaw = car.transform.eulerAngles.y;
        SpawnCar((carIndex + 1) % CarSpec.All.Length, new Vector3(p.x, 0, p.z), yaw, false);
    }

    // ------------------------------------------------------------ frame
    void Update()
    {
        if (world == null || !world.Ready || car == null) return;
        var kbM = Keyboard.current; var gpM = Gamepad.current;
        if (!smoke && ((kbM != null && kbM.mKey.wasPressedThisFrame) || (gpM != null && gpM.selectButton.wasPressedThisFrame))) { map.Toggle(); }
        world.UpdateStreaming(shotFocus.HasValue ? shotFocus.Value : (map.Active ? map.Focus : car.transform.position));
        if (map.Active) { map.Tick(); return; }
        HandleCameraInput(); HandleCoordinateKeys();
        if (carVisual != null) { carVisual.brakeGlow.SetActive(car.Brake > 0.1f && car.Gear > 0 || car.Gear < 0 && car.Throttle > 0.1f); carVisual.reverseGlow.SetActive(car.Gear < 0); }
        if (Time.unscaledDeltaTime > 0.028f && Time.frameCount > 120) Log.I("perf", $"slow frame {Time.unscaledDeltaTime * 1000f:F0} ms  streamToggle={(world.LastToggleFrame == Time.frameCount)}  gc={System.GC.CollectionCount(0)}  speed={(car != null ? car.SpeedKmh : 0):F0}");
        fpsAcc += Time.unscaledDeltaTime; fpsN++; fpsMin = Mathf.Min(fpsMin, 1f / Mathf.Max(Time.unscaledDeltaTime, 1e-4f));
        if (Time.unscaledTime > fpsNext) { fps = fpsN / fpsAcc; fpsAcc = 0; fpsN = 0; fpsNext = Time.unscaledTime + 0.5f; }
        if (autoOn && auto != null) { auto.Drive(Time.deltaTime); if (Time.unscaledTime > telemetryNext) { Log.I("auto", auto.Status); Telemetry(); } if (!smoke) HandleToggles(); return; }
        if (smoke || scriptedTest) return;

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
            if (kb.fKey.wasPressedThisFrame) CycleCar();
            if (kb.yKey.wasPressedThisFrame && traffic != null) traffic.SetEnabled(!traffic.Enabled);
            if (kb.vKey.wasPressedThisFrame) { QualitySettings.vSyncCount = 1 - QualitySettings.vSyncCount; Log.I("gfx", "vsync " + (QualitySettings.vSyncCount == 1 ? "ON" : "OFF")); copiedNote = "vsync " + (QualitySettings.vSyncCount == 1 ? "on" : "off"); copiedAt = Time.unscaledTime; }
            if (kb.tKey.wasPressedThisFrame) { car.AssistMode = (Assist)(((int)car.AssistMode + 1) % 4); Log.I("car", "assist mode " + car.AssistMode); }
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
        UpdateHaptics();
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
            if (gp.dpad.right.wasPressedThisFrame) CycleCar();
            Vector2 rs = gp.rightStick.ReadValue();
            if (rs.magnitude > 0.2f) cam.Look(rs * 170f * Time.unscaledDeltaTime);
            back |= gp.rightStickButton.isPressed;
        }
        cam.LookBack(back);
    }

    float rumbleImpact;
    /// <summary>DualSense / gamepad rumble: low motor = tyre slip and wheelspin, high motor = ABS / traction-control chatter and impacts.</summary>
    void UpdateHaptics()
    {
        var gp = pad; if (gp == null) return;
        try
        {
            if (map != null && map.Active || Time.timeScale == 0f) { gp.SetMotorSpeeds(0f, 0f); return; }
            float slip = car.SlipAmount * Mathf.Clamp01(car.SpeedKmh / 25f);
            float low = Mathf.Clamp01(slip * 0.55f + (car.Rpm > car.Spec.Redline * 0.97f ? 0.12f : 0f));
            float high = Mathf.Clamp01((car.AbsActive || car.TcsActive ? 0.35f : 0f) + rumbleImpact);
            rumbleImpact = Mathf.Max(0f, rumbleImpact - 2.5f * Time.unscaledDeltaTime);
            gp.SetMotorSpeeds(low, high);
        }
        catch (System.Exception) { /* not every controller supports rumble */ }
    }

    static float Deadzone(float v, float dz) => Mathf.Abs(v) < dz ? 0 : Mathf.Sign(v) * (Mathf.Abs(v) - dz) / (1 - dz);
    static float Curve(float v) => Mathf.Sign(v) * (0.35f * Mathf.Abs(v) + 0.65f * v * v);   // finer control near centre

    void Telemetry()
    {
        telemetryNext = Time.unscaledTime + 1f;
        var p = car.transform.position;
        Log.I("tel", $"pos=({p.x:F0},{p.y:F1},{p.z:F0}) {car.SpeedKmh:F0}km/h fwd={car.ForwardSpeed:F1}m/s wheels={car.WheelsOnGround}/4 surf={car.CurrentSurface} " +
                     $"in[steer={steerIn:F2} thr={thrIn:F2} brk={brkIn:F2} hand={handIn}] gear={car.Gear} rpm={car.Rpm:F0} audioPeak={(audio != null ? audio.Synth.LastPeak : 0):F2} buffers={(audio != null ? audio.Synth.Buffers : 0)} gc={System.GC.CollectionCount(0)} fx={(fx != null ? fx.Strength : 0):F2} smoke={(tyres != null ? tyres.Alive : 0)} wfx=[{car.WheelFx[0]:F1},{car.WheelFx[1]:F1},{car.WheelFx[2]:F1},{car.WheelFx[3]:F1}] cam={(cam != null ? cam.ModeName : "-")} fov={(mainCam != null ? mainCam.fieldOfView : 0):F0} pad={padName} focus={Application.isFocused} kb={(Keyboard.current != null)} auto={autoOn} fps={fps:F0} (min {fpsMin:F0}) mem={System.GC.GetTotalMemory(false) / 1048576}MB");
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
        Check("border clamp east", map.Pos.x <= WorldData.MaxX - 5f && map.Pos.x > 3150f, $"x={map.Pos.x:F1} (limit {WorldData.MaxX - 6f:F0})");
        map.DebugSetPos(0f, -3190f);
        yield return Hold(kb, Key.S, 0.8f);
        Check("border clamp south", map.Pos.y >= WorldData.Z0 + 5f && map.Pos.y < -3150f, $"z={map.Pos.y:F1}");

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

    /// <summary>Views of one spot (local x,z): straight down, oblique from the south-west, and low along the nearest road. Saved to docs/shots/at_*.png.</summary>
    IEnumerator ShotAt(float x, float z)
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "docs", "shots")); Directory.CreateDirectory(dir);
        var camera = mainCam; cam.enabled = false; map.enabled = false;
        var rt = new RenderTexture(1600, 900, 24); camera.targetTexture = rt; camera.fieldOfView = 55f;
        var tex = new Texture2D(1600, 900, TextureFormat.RGB24, false);
        shotFocus = new Vector3(x, 0, z);                   // Update() streams around this, not around the car (else it unloads what we are loading)
        yield return world.LoadAround(new Vector3(x, 0, z), 900f, 1500f);
        float g = world.Data.TerrainHeight(x, z);
        if (System.Environment.GetEnvironmentVariable("BERAT_POKE") != null)
        {   // where does the terrain mesh stand above the drawn road (road y + lift)? one char per metre: '#' terrain > road+3 cm, '.' fine, ' ' no road
            var sb = new StringBuilder();
            for (float zz = z + 10; zz >= z - 10; zz -= 1f)
            {
                for (float xx = x - 12; xx <= x + 12; xx += 1f)
                {
                    world.Roads.Query(xx, zz, 400f, out float dk, out float ry, out float rw);
                    float top = !float.IsNaN(dk) ? dk : (rw > 0.99f ? ry : float.NaN);
                    float th = world.Data.TerrainHeight(xx, zz);
                    sb.Append(float.IsNaN(top) ? ' ' : (th > top + WorldBuilder.RoadLift + 0.0f ? '#' : '.'));
                }
                sb.AppendLine();
            }
            Log.I("poke", "terrain above road surface ('#')\n" + sb);
        }
        Vector2 rdir = Vector2.up; world.Roads.NearestRoad(x, z, 30f, out rdir);
        var views = new (string n, Vector3 p, Vector3 look)[] {
            ("top", new Vector3(x, g + 60f, z - 0.1f), new Vector3(x, g, z)),
            ("oblique", new Vector3(x - 22f, g + 16f, z - 22f), new Vector3(x, g, z)),
            ("road", new Vector3(x - rdir.x * 22f, g + 2.2f, z - rdir.y * 22f), new Vector3(x + rdir.x * 20f, g + 1f, z + rdir.y * 20f)) };
        foreach (var v in views)
        {
            camera.transform.position = v.p; camera.transform.LookAt(v.look); shotFocus = v.p; world.ForceStream();
            yield return world.LoadAround(new Vector3(x, 0, z), 900f, 1500f); world.UpdateStreaming(v.p); yield return null; yield return null;
            camera.Render(); RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0); tex.Apply(); RenderTexture.active = null;
            File.WriteAllBytes(Path.Combine(dir, $"at_{v.n}.png"), tex.EncodeToPNG());
        }
        Log.I("shotat", "done"); Application.Quit();
    }

    /// <summary>Opens the map at several zoom levels and saves the game window (with the GUI: grid, labels) to docs/shots/map_*.png. Only the game's own framebuffer is captured.</summary>
    IEnumerator MapShots()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "docs", "shots")); Directory.CreateDirectory(dir);
        yield return new WaitForSecondsRealtime(1.5f);
        map.Toggle();
        foreach (int lvl in new[] { 6, 8, 9, 10, 11, 12 })
        {
            map.TestZoom(lvl, new Vector2(0, 0));
            world.ForceStream();
            for (int i = 0; i < 20; i++) yield return null;
            yield return new WaitForSecondsRealtime(1.2f);
            ScreenCapture.CaptureScreenshot(Path.Combine(dir, $"map_{lvl:D2}.png"));
            yield return new WaitForSecondsRealtime(0.6f);
        }
        Log.I("mapshots", "done"); Application.Quit();
    }

    /// <summary>Lets the traffic spawn, then saves the game window (the game's own framebuffer only) a few times to docs/shots/traffic_*.png.</summary>
    IEnumerator TrafficShots()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "docs", "shots")); Directory.CreateDirectory(dir);
        yield return new WaitForSecondsRealtime(28f);
        for (int i = 0; i < 4; i++)
        {
            ScreenCapture.CaptureScreenshot(Path.Combine(dir, $"traffic_{i}.png"));
            Log.I("trafficshots", $"shot {i}: {traffic?.Count} cars, mean {traffic?.MeanSpeedKmh:F0} km/h");
            yield return new WaitForSecondsRealtime(6f);
        }
        Application.Quit();
    }

    /// <summary>
    /// Autopilot soak test (-autotest [seconds]): drives from the spawn point with traffic, logs the car's position once a second (game time) and reports how far and how widely it got,
    /// how often it got stuck or turned round, and which 100 m cells it visited more than 3 times (a loop).
    /// </summary>
    IEnumerator AutoTest()
    {
        var args = System.Environment.GetCommandLineArgs(); int ai = System.Array.IndexOf(args, "-autotest");
        float total = 180f; if (ai + 1 < args.Length && float.TryParse(args[ai + 1], out float tt)) total = tt;
        Time.timeScale = 4f;
        yield return new WaitForSecondsRealtime(1f);
        auto = new Autopilot(world.Data, car); autoOn = true;
        var visits = new Dictionary<long, int>(); var cells = new HashSet<long>(); float dist = 0f; Vector3 last = car.transform.position; long lastCell = long.MinValue; float t0 = Time.time, tick = t0;
        while (Time.time - t0 < total)
        {
            yield return null;
            var p = car.transform.position; dist += Vector2.Distance(new Vector2(p.x, p.z), new Vector2(last.x, last.z)); last = p;
            long cell = ((long)Mathf.FloorToInt(p.x / 100f) << 20) ^ (uint)Mathf.FloorToInt(p.z / 100f);
            if (cell != lastCell) { visits.TryGetValue(cell, out int c); visits[cell] = c + 1; lastCell = cell; }
            cells.Add(cell);
            if (Time.time - tick >= 1f)
            {
                tick = Time.time;
                var f = auto.Follower;
                Log.I("autotest", $"t={Time.time - t0:F0} pos=({p.x:F0},{p.z:F0}) {car.SpeedKmh:F0} km/h piece {(f != null ? f.road.fid : 0)} s {(f != null ? f.s : 0):F0} traffic {(traffic != null ? traffic.Count : 0)}");
            }
        }
        int loops = 0; foreach (var kv in visits) if (kv.Value > 3) loops++;
        Log.I("autotest", $"RESULT {total:F0} s: {dist:F0} m driven (avg {dist / total * 3.6f:F0} km/h), {cells.Count} distinct 100 m cells, {loops} cells entered more than 3 times, stuck {auto.StuckEvents}, turn-arounds {auto.TurnArounds}, relocations {auto.Relocations}");
        Application.Quit();
    }

    IEnumerator MinimapShot()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "docs", "shots")); Directory.CreateDirectory(dir);
        yield return new WaitForSecondsRealtime(2f);
        minimap.SaveSnapshot(Path.Combine(dir, "minimap.png"));
        Log.I("minimapshot", "done"); Application.Quit();
    }

    /// <summary>Renders the horizon twice, 0.5 m apart, with one layer hidden at a time, so the flicker between the two frames can be measured per layer.</summary>
    IEnumerator SparkleTest()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "docs", "sparkle")); Directory.CreateDirectory(dir);
        var camera = mainCam; cam.enabled = false; map.enabled = false;
        var rt = new RenderTexture(1600, 900, 24); camera.targetTexture = rt; camera.fieldOfView = 60f;
        var tex = new Texture2D(1600, 900, TextureFormat.RGB24, false);
        Vector3 at = new Vector3(350f, 280f, -426f);      // open field south-east of the village: an unobstructed view to the horizon
        shotFocus = at; yield return world.LoadAround(new Vector3(at.x, 0, at.z), 1500f, 5000f);
        foreach (string cfg in new[] { "none", "buildings", "water", "far", "roads", "terrainlow", "buildings,water,roads" })
            for (int dirIdx = 0; dirIdx < 2; dirIdx++)
                for (int frame = 0; frame < 2; frame++)
                {
                    world.DebugHide = cfg; world.ForceStream();
                    Quaternion look = Quaternion.Euler(dirIdx == 0 ? 25f : 22f, dirIdx == 0 ? 0f : 60f, 0f);
                    camera.transform.position = at + look * Vector3.right * (0.5f * frame); camera.transform.rotation = look;
                    world.UpdateStreaming(at); yield return null; yield return null;
                    camera.Render();
                    RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0); tex.Apply(); RenderTexture.active = null;
                    File.WriteAllBytes(Path.Combine(dir, $"{cfg.Replace(',', '+')}_{dirIdx}_{frame}.png"), tex.EncodeToPNG());
                }
        world.DebugHide = "";
        Log.I("sparkle", "done"); Application.Quit();
    }

    /// <summary>Streaming showcase: chase view, long view over mid + far terrain, 8 km overview, and the nearest water. Renders to docs/shots/world_*.png.</summary>
    IEnumerator WorldShots()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "docs", "shots")); Directory.CreateDirectory(dir);
        var camera = mainCam; cam.enabled = false; map.enabled = false;
        var rt = new RenderTexture(1600, 900, 24); camera.targetTexture = rt; camera.fieldOfView = 55f;
        var tex = new Texture2D(1600, 900, TextureFormat.RGB24, false);
        var sp = world.Data.Spawn; Vector3 at = new Vector3(sp.x, sp.y, sp.z); Vector3 fwd = Quaternion.Euler(0, sp.heading, 0) * Vector3.forward;
        // nearest water polygon among the loaded chunks
        Vector3? water = null; float bestD = 1e9f;
        foreach (var ch in world.Data.Chunks.Values)
            foreach (var a in ch.Areas)
            {
                if (a.ring.Length < 12) continue;
                Vector2 c = Vector2.zero; int n = a.ring.Length / 2; for (int i = 0; i < n; i++) c += new Vector2(a.ring[i * 2], a.ring[i * 2 + 1]); c /= n;
                float d = Vector2.Distance(c, new Vector2(at.x, at.z));
                if (d < bestD) { bestD = d; water = new Vector3(c.x, a.level, c.y); }
            }
        Vector3? stream = null; bestD = 1e9f;
        foreach (var ch in world.Data.Chunks.Values)
            foreach (var l in ch.Lines)
            {
                if (l.hw < 3f) continue;
                Vector3 m = new Vector3(l.pts[(l.pts.Length / 6) * 3], l.pts[(l.pts.Length / 6) * 3 + 1], l.pts[(l.pts.Length / 6) * 3 + 2]);
                float d = Vector2.Distance(new Vector2(m.x, m.z), new Vector2(at.x, at.z));
                if (d < bestD) { bestD = d; stream = m; }
            }
        Log.I("worldshots", $"water area at {(water.HasValue ? water.Value.ToString() : "none")}, wide stream at {(stream.HasValue ? stream.Value.ToString() : "none")}");
        var views = new System.Collections.Generic.List<(string name, Vector3 pos, Vector3 look, float nearR, float midR)>
        {
            ("world_chase", car.transform.position - car.transform.forward * 5.3f + Vector3.up * 2.4f, car.transform.position + Vector3.up * 1.1f + car.transform.forward * 3f, 1500f, 5000f),
            ("world_long_view", at + Vector3.up * 70f, at + fwd * 6000f + Vector3.up * 40f, 1500f, 5000f),
            ("world_overview_8km", at + Vector3.up * 7000f - fwd * 6000f, at + fwd * 2000f, 800f, 5000f),
            ("world_horizon_40km", at + Vector3.up * 1200f, at + fwd * 30000f + Vector3.up * 300f, 800f, 5000f),
        };
        if (water.HasValue) views.Add(("world_water", water.Value + new Vector3(0, 35f, -70f), water.Value, 1500f, 5000f));
        if (stream.HasValue) views.Add(("world_stream", stream.Value + new Vector3(0, 14f, -22f), stream.Value, 1500f, 5000f));
        foreach (var v in views)
        {
            camera.transform.position = v.pos; camera.transform.LookAt(v.look);
            shotFocus = v.pos; world.ForceStream();
            yield return world.LoadAround(new Vector3(v.pos.x, 0, v.pos.z), v.nearR, v.midR);
            world.UpdateStreaming(shotFocus.Value); yield return null; yield return null; yield return new WaitForSecondsRealtime(0.3f);
            camera.Render();
            RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0); tex.Apply(); RenderTexture.active = null;
            File.WriteAllBytes(Path.Combine(dir, v.name + ".png"), tex.EncodeToPNG());
            Log.I("worldshots", $"{v.name}: chunks={world.LoadedMid} near={world.LoadedNear}");
        }
        Log.I("worldshots", "done"); Application.Quit();
    }
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

    /// <summary>Renders every car model from three angles (front 3/4, side, rear 3/4) to docs/shots/car_*.png.</summary>
    IEnumerator CarShots()
    {
        string dir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "docs", "shots")); Directory.CreateDirectory(dir);
        cam.enabled = false; map.enabled = false;
        var rt = new RenderTexture(1600, 900, 24); mainCam.targetTexture = rt; mainCam.fieldOfView = 38f; var tex = new Texture2D(1600, 900, TextureFormat.RGB24, false);
        var sp = world.Data.Spawn; Vector3 at = new Vector3(sp.x, 0, sp.z);
        for (int idx = 0; idx < CarSpec.All.Length; idx++)
        {
            SpawnCar(idx, at, sp.heading, false);
            car.Throttle = car.Brake = car.Steer = 0; shotFocus = car.transform.position; nextForce();
            yield return new WaitForSeconds(1.2f);
            var spec = car.Spec; Vector3 c = car.transform.position + car.transform.TransformDirection(new Vector3(0, spec.GroundY + spec.Height * 0.5f, spec.CentreOfMass.z * 0f));
            c = car.transform.position + Vector3.up * (spec.GroundY + spec.Height * 0.45f);
            float dist = spec.Length * 1.55f;
            foreach (var (name, az, el) in new[] { ("front34", 35f, 9f), ("side", 90f, 4f), ("rear34", 148f, 12f) })
            {
                Vector3 pos = default;
                foreach (float sgn in new[] { 1f, -1f })                                   // try the wanted side, then the other one if a building is in the way
                    foreach (float dm in new[] { 1f, 0.8f, 0.6f })
                    {
                        Vector3 dir3 = Quaternion.Euler(0, car.transform.eulerAngles.y + az * sgn, 0) * Vector3.forward;   // az measured from the car's nose
                        pos = c + Quaternion.Euler(-el, 0, 0) * dir3 * dist * dm; pos.y = Mathf.Max(pos.y, c.y + 0.4f);
                        Physics.SyncTransforms();
                        if (!Physics.CheckSphere(pos, 0.6f) && !Physics.Linecast(pos, c + car.transform.right * 0.3f)) goto placed;
                    }
                placed:
                mainCam.transform.position = pos; mainCam.transform.LookAt(c);
                mainCam.Render();
                RenderTexture.active = rt; tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0); tex.Apply(); RenderTexture.active = null;
                File.WriteAllBytes(Path.Combine(dir, $"car_{idx}_{name}.png"), tex.EncodeToPNG());
            }
            Log.I("carshots", $"{spec.Name}: rendered, ride height check: wheels on ground {car.WheelsOnGround}/4, body y {car.transform.position.y - world.GroundHeight(car.transform.position.x, car.transform.position.z, car.transform.position.y, out _):F2} m above road");
        }
        Log.I("carshots", "done"); Application.Quit();
    }

    /// <summary>Samples the physics ground function every 0.25 m along the main road and compares road-ribbon vs terrain-mesh profiles.</summary>
    IEnumerator GroundScan()
    {
        yield return new WaitForSeconds(0.5f);
        RoadData road = null; foreach (var r0 in world.Data.Roads) if (!r0.bridge && (road == null || r0.pts.Length > road.pts.Length)) road = r0; int n = road.pts.Length / 3;
        var pts = new List<Vector3>(); for (int i = 0; i < n; i++) pts.Add(new Vector3(road.pts[i * 3], road.pts[i * 3 + 1], road.pts[i * 3 + 2]));
        var prof = new List<Vector3>();      // x = s, y = ribbon-mode height, z = terrain-mode height
        float acc = 0; Vector3 prev = pts[0];
        for (int i = 1; i < pts.Count; i++)
        {
            float L = Vector3.Distance(new Vector3(pts[i].x, 0, pts[i].z), new Vector3(prev.x, 0, prev.z));
            for (float d = 0; d < L; d += 0.25f)
            {
                var p = Vector3.Lerp(new Vector3(prev.x, 0, prev.z), new Vector3(pts[i].x, 0, pts[i].z), d / Mathf.Max(L, 1e-3f));
                world.TerrainPhysicsOnly = false; float a = world.GroundHeight(p.x, p.z, 400f, out _);
                world.TerrainPhysicsOnly = true; float b = world.GroundHeight(p.x, p.z, 400f, out _);
                prof.Add(new Vector3(acc + d, a, b));
            }
            acc += L; prev = pts[i];
        }
        Vector2 Rough(bool ribbon)
        {   // second derivative (curvature) statistics over 2 m baseline: what a wheel rolling at speed converts into vertical acceleration
            double sum = 0; float mx = 0; int c = 0;
            for (int i = 8; i + 8 < prof.Count; i++)
            {
                float y0 = ribbon ? prof[i - 8].y : prof[i - 8].z, y1 = ribbon ? prof[i].y : prof[i].z, y2 = ribbon ? prof[i + 8].y : prof[i + 8].z;
                float k = (y0 - 2 * y1 + y2) / 4f; sum += k * k; mx = Mathf.Max(mx, Mathf.Abs(k)); c++;
            }
            return new Vector2(Mathf.Sqrt((float)(sum / Mathf.Max(c, 1))), mx);
        }
        var rr = Rough(true); var tr = Rough(false);
        Log.I("groundscan", $"road #269, {acc:F0} m, {prof.Count} samples. curvature RMS  ribbon {rr.x:F5} 1/m (max {rr.y:F4})   terrain {tr.x:F5} 1/m (max {tr.y:F4})   at 25 m/s: ribbon {rr.x * 625f:F2} m/s2 vs terrain {tr.x * 625f:F2} m/s2");
        for (int i = 100; i < 260 && i < prof.Count; i += 8) Log.I("groundscan", $"  s={prof[i].x:F1}  ribbon {prof[i].y:F3}  terrain {prof[i].z:F3}  diff {prof[i].y - prof[i].z:+0.000;-0.000}");
        Application.Quit();
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
            if (!autoOn && smokeT > 1f) { auto = new Autopilot(world.Data, car); autoOn = true; var a2 = System.Environment.GetCommandLineArgs(); int ai = System.Array.IndexOf(a2, "-autoKmh"); if (ai >= 0 && ai + 1 < a2.Length && float.TryParse(a2[ai + 1], out float ak)) { auto.TargetKmh = ak; auto.ObeyLimits = false; } }
            steerIn = car.Steer; thrIn = car.Throttle;
            foreach (float t in new[] { 4f, 12f, 20f })
                if (smokeT >= t && smokeT - Time.deltaTime < t) ScreenCapture.CaptureScreenshot(Path.Combine(dir, $"shot_{(int)t}.png"));
            if (Time.unscaledTime > telemetryNext) Telemetry();
            yield return null;
        }
        Log.I("smoke", "done");
        Application.Quit();
    }

    // ------------------------------------------------------------ position / bug-report coordinates
    string copiedNote; float copiedAt = -10f;
    GUIStyle stRight, stRightBig, stRightNote, stGauge, stCarName, stCarTag, stCamName, stAuto;

    void DrawPositionBox()
    {
        var p = car.transform.position; var c = Grid.CellOf(p.x, p.z);
        world.GroundHeight(p.x, p.z, p.y, out Surface surf);
        float w = 330f, h = 84f; var r = new Rect(Screen.width - w - 14, 10, w, h);
        GUI.color = new Color(0, 0, 0, 0.5f); GUI.DrawTexture(r, Texture2D.whiteTexture); GUI.color = Color.white;
        if (stRight == null) { stRight = new GUIStyle(small) { alignment = TextAnchor.UpperRight, fontSize = 15 }; stRightBig = new GUIStyle(stRight) { fontSize = 20, fontStyle = FontStyle.Bold }; stRightNote = new GUIStyle(stRight) { fontSize = 13 }; stRightNote.normal.textColor = new Color(0.6f, 1f, 0.6f); }
        var st = stRight; var big2 = stRightBig;
        GUI.Label(new Rect(r.x, r.y + 4, w - 10, 26), $"cell ({c.x}, {c.y})", big2);
        GUI.Label(new Rect(r.x, r.y + 30, w - 10, 22), $"x {p.x:F1}   z {p.z:F1}   elev {p.y:F1} m", st);
        GUI.Label(new Rect(r.x, r.y + 50, w - 10, 22), $"{Grid.Lambert(p.x, p.z)}   {surf}", st);
        if (Time.unscaledTime - copiedAt < 2.5f) GUI.Label(new Rect(r.x, r.y + h + 2, w - 10, 20), copiedNote, stRightNote);
    }

    /// <summary>K: copy this spot as a one-line report (also logged). J: jump to coordinates found on the clipboard.</summary>
    void HandleCoordinateKeys()
    {
        var kb = Keyboard.current; if (kb == null) return;
        if (kb.kKey.wasPressedThisFrame)
        {
            var p = car.transform.position; world.GroundHeight(p.x, p.z, p.y, out Surface surf);
            string rep = Grid.Report(p, car.transform.eulerAngles.y, car.Spec.Name, surf.ToString());
            GUIUtility.systemCopyBuffer = rep; Log.I("spot", rep); copiedNote = "copied to clipboard"; copiedAt = Time.unscaledTime;
        }
        if (kb.jKey.wasPressedThisFrame)
        {
            if (Grid.TryParse(GUIUtility.systemCopyBuffer, out float x, out float z)) { TeleportCar(x, z); copiedNote = $"jumped to {x:F0}, {z:F0}"; }
            else copiedNote = "clipboard has no coordinates (x=.. z=.. or cell (a, b))";
            copiedAt = Time.unscaledTime; Log.I("spot", copiedNote);
        }
    }

    /// <summary>Places the car on the ground at (x, z), aligned to the nearest road if there is one within 7 m.</summary>
    void TeleportCar(float x, float z)
    {
        x = Mathf.Clamp(x, WorldData.X0 + 8f, WorldData.MaxX - 8f); z = Mathf.Clamp(z, WorldData.Z0 + 8f, WorldData.MaxZ - 8f);
        float yaw = car.transform.eulerAngles.y;
        if (world.Roads.NearestRoad(x, z, 7f, out Vector2 dir)) { float a = Mathf.Atan2(dir.x, dir.y) * Mathf.Rad2Deg; yaw = Mathf.Abs(Mathf.DeltaAngle(a, yaw)) <= 90f ? a : a + 180f; }
        float g = world.GroundHeight(x, z, world.Data.TerrainHeight(x, z) + 1f, out _);
        car.Respawn(new Vector3(x, g + car.Spec.WheelRadiusSum + 0.25f, z), yaw); cam.Snap();
        Log.I("spot", $"teleported to x={x:F1} z={z:F1} cell={Grid.CellLabel(x, z)}");
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
        if (stGauge == null) stGauge = new GUIStyle(small) { alignment = TextAnchor.UpperCenter, fontSize = 12 };
        var lbl = stGauge;
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
        float carAge = Time.unscaledTime - shownCarAt;
        if (carAge < 3.5f) { if (stCarName == null) { stCarName = new GUIStyle(big) { fontSize = 34, alignment = TextAnchor.UpperCenter }; stCarTag = new GUIStyle(small) { alignment = TextAnchor.UpperCenter, fontSize = 18 }; } stCarName.normal.textColor = stCarTag.normal.textColor = new Color(1, 1, 1, Mathf.Clamp01(3.5f - carAge)); GUI.Label(new Rect(0, 70, Screen.width, 50), car.Spec.Name, stCarName); GUI.Label(new Rect(0, 118, Screen.width, 30), car.Spec.Tagline, stCarTag); }
        float camAge = Time.unscaledTime - cam.ModeShownAt;
        if (camAge < 2.2f) { if (stCamName == null) stCamName = new GUIStyle(big) { fontSize = 26, alignment = TextAnchor.LowerCenter }; stCamName.normal.textColor = new Color(1, 1, 1, Mathf.Clamp01(2.2f - camAge)); GUI.Label(new Rect(0, Screen.height - 90, Screen.width, 60), "Camera: " + cam.ModeName, stCamName); }
        DrawGauges();
        if (minimap != null) minimap.DrawGUI();
        if (autoOn) { if (stAuto == null) { stAuto = new GUIStyle(big) { fontSize = 30, alignment = TextAnchor.UpperCenter }; stAuto.normal.textColor = new Color(1f, 0.85f, 0.2f); } GUI.Label(new Rect(0, 14, Screen.width, 44), "AUTOPILOT ON  (P / Circle to take over)", stAuto); }
        DrawPositionBox();
        GUI.Label(new Rect(16, 12, 700, 24), $"{fps:F0} fps   |   pad: {padName}   |   gear {(car.Gear < 0 ? "R" : car.Gear == 0 ? "N" : car.Gear.ToString())}  {car.Rpm:F0} rpm   assist {car.AssistMode}{(car.AbsActive ? " ABS" : "")}{(car.TcsActive ? " TCS" : "")}   traffic {(traffic != null && traffic.Enabled ? traffic.Count + " cars" : "off")}   vol {(audio != null ? audio.Volume * 100 : 0):F0}%{(audio != null && audio.Synth.Muted ? " [muted]" : "")}", small);
        if (showHelp)
            GUI.Label(new Rect(16, 36, 1100, 170), "Berat (31370) — LiDAR HD + BD TOPO\nDrive: W/S A/D  or  R2 / L2 + left stick     Handbrake: Space / Square / R1     Reset: R / Triangle     Autopilot: P / Circle     MAP: M / Select     Camera: C / D-pad up   Look: right stick / right-drag   Rear: B / R3   Car: F / D-pad right   Copy spot: K   Jump to clipboard coords: J   V-sync: V   Assists: T   Volume: [ ]   Mute: N\nHelp: H / Options     Debug: F3     Quit: Esc", small);
        if (showDebug)
        {
            var sb = new StringBuilder();
            var p = car.transform.position;
            sb.AppendLine($"pos {p.x:F1},{p.y:F1},{p.z:F1}   cell {Grid.CellLabel(p.x, p.z)}   surf {car.CurrentSurface}   wheels {car.WheelsOnGround}/4");
            sb.AppendLine($"input steer {steerIn:F2} thr {thrIn:F2} brk {brkIn:F2} hand {handIn}");
            sb.AppendLine($"log: {Log.Path_}");
            GUI.Label(new Rect(minimap != null ? minimap.Area.xMax + 16f : 16f, Screen.height - 90, 900, 80), sb.ToString(), mono);
        }
    }
}
