using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using UnityEngine.InputSystem.Layouts;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.InputSystem.Utilities;

/// <summary>
/// Steering wheel and pedals (Logitech G29 / G920 / G923, Thrustmaster, Fanatec, Moza...). They reach the Input System as plain HID
/// joysticks whose axes and buttons differ per model, per mode switch and per driver, so nothing is guessed: a short on-screen setup
/// learns which axis is the wheel, which are the pedals (with their rest and full positions) and which buttons do what, and saves it
/// per device (wheel-&lt;device&gt;.json next to the log). The setup opens by itself the first time an unknown wheel moves, and on L.
/// No force feedback: the Input System cannot drive HID force feedback.
/// </summary>
public class WheelInput
{
    /// <summary>What the setup learned. Control paths are relative to the device ("stick/x", "rz", "button5", "hat/up").</summary>
    [System.Serializable]
    public class Mapping
    {
        public string device;
        public string steer; public float steerCentre, steerPerDeg;          // steerPerDeg is signed: it also says which way is right
        public float lockDeg = 180f;                                         // wheel rotation each way for full steering lock (edit in the file)
        public string throttle; public float throttleRest, throttleFull;
        public string brake; public float brakeRest, brakeFull;
        public string handbrake = "", reset = "", camera = "", lookBack = "";
    }

    enum Step { Off, Centre, QuarterTurn, Throttle, Brake, Handbrake, Reset, Camera, LookBack }
    static readonly Step[] ButtonSteps = { Step.Handbrake, Step.Reset, Step.Camera, Step.LookBack };

    public Joystick Device { get; private set; }
    public string Name => Device != null ? Device.displayName : "none";
    /// <summary>Mapped and touched since it was found: until then its axes may still read the zeroed state of a device that has not reported yet.</summary>
    public bool Active => Device != null && map != null && engaged && step == Step.Off;
    public bool SetupActive => step != Step.Off;
    public bool Mapped => map != null;
    public float Steer { get; private set; }
    public float Throttle { get; private set; }
    public float Brake { get; private set; }
    public bool Handbrake { get; private set; }
    public bool ResetPressed { get; private set; }
    public bool CameraPressed { get; private set; }
    public bool LookBack { get; private set; }

    Mapping map;
    bool engaged;
    List<AxisControl> axes = new List<AxisControl>();
    List<ButtonControl> buttons = new List<ButtonControl>();
    float[] snapshot;                                                        // axis values when the device was found (engagement) or centred (setup)
    Step step;
    Mapping draft;
    string problem;                                                          // why the last confirm was refused, shown under the prompt
    float problemAt;

    public static string ConfigDir => Log.Path_ != null ? Path.GetDirectoryName(Log.Path_) : Application.persistentDataPath;
    public static string ConfigPath(InputDevice d)
    {
        string name = string.IsNullOrEmpty(d.description.product) ? d.displayName : d.description.product;
        foreach (char c in Path.GetInvalidFileNameChars()) name = name.Replace(c, '_');
        return Path.Combine(ConfigDir, "wheel-" + name.Replace(' ', '_') + ".json");
    }

    // ------------------------------------------------------------ device
    /// <summary>Joysticks that are not gamepads, a wheel with a saved setup first.</summary>
    void PickDevice()
    {
        if (Device != null && Device.added) return;
        if (Device != null) { Log.I("wheel", $"wheel gone: {Name}"); Device = null; map = null; step = Step.Off; }
        Joystick pick = null;
        foreach (var j in Joystick.all)
        {
            if (!j.enabled) continue;
            if (pick == null || File.Exists(ConfigPath(j)) && !File.Exists(ConfigPath(pick))) pick = j;
        }
        if (pick == null) return;
        Device = pick; engaged = false;
        axes = Device.allControls.OfType<AxisControl>().Where(c => !(c is ButtonControl) && !c.synthetic && !c.noisy && !(c.parent is DpadControl)).ToList();
        buttons = Device.allControls.OfType<ButtonControl>().Where(c => !(c.parent is StickControl) && !c.synthetic && !c.noisy).ToList();
        snapshot = ReadAxes();
        map = Load(Device);
        Log.I("wheel", $"wheel found: '{Name}' layout={Device.layout} product='{Device.description.product}' setup={(map != null ? ConfigPath(Device) : "none (opens when the wheel moves, or press L)")}");
        Log.I("wheel", "axes: " + string.Join(", ", axes.Select(Rel)) + "   buttons: " + string.Join(", ", buttons.Select(Rel)));
    }

    string Rel(InputControl c) => c.path.Substring(Device.path.Length + 1);
    /// <summary>Normalised but without processors: the Joystick layout puts a radial stick deadzone on "stick", and on a G29 that
    /// stick is the wheel (x) and the throttle (y), so the processed wheel value would shrink and vanish as the throttle moves.</summary>
    static float Raw(AxisControl a) => a.ReadUnprocessedValue();
    float[] ReadAxes() { var v = new float[axes.Count]; for (int i = 0; i < v.Length; i++) v[i] = Raw(axes[i]); return v; }
    float Axis(string path) => Device.TryGetChildControl<AxisControl>(path) is AxisControl a ? Raw(a) : 0f;
    ButtonControl Button(string path) => string.IsNullOrEmpty(path) ? null : Device.TryGetChildControl<ButtonControl>(path);

    static Mapping Load(InputDevice d)
    {
        string path = ConfigPath(d);
        if (!File.Exists(path)) return null;
        try
        {
            var m = JsonUtility.FromJson<Mapping>(File.ReadAllText(path));
            bool ok = m != null && d.TryGetChildControl<AxisControl>(m.steer) != null && d.TryGetChildControl<AxisControl>(m.throttle) != null && d.TryGetChildControl<AxisControl>(m.brake) != null && m.steerPerDeg != 0f && m.lockDeg > 0f;
            if (ok) return m;
            Log.I("wheel", $"setup in {path} does not fit this device: ignored");
        }
        catch (System.Exception e) { Log.I("wheel", $"cannot read {path}: {e.Message}"); }
        return null;
    }

    void Save(Mapping m)
    {
        string path = ConfigPath(Device);
        try { File.WriteAllText(path, JsonUtility.ToJson(m, true)); Log.I("wheel", $"setup saved to {path}"); }
        catch (System.Exception e) { Log.I("wheel", $"cannot save {path}: {e.Message}"); }
    }

    // ------------------------------------------------------------ frame
    /// <summary>Once per frame, before reading the outputs.</summary>
    public void Tick()
    {
        Steer = Throttle = Brake = 0f; Handbrake = ResetPressed = CameraPressed = LookBack = false;
        PickDevice();
        if (Device == null) return;
        if (!engaged && Moved()) { engaged = true; Log.I("wheel", "wheel in use"); if (map == null) StartSetup(); }
        if (step != Step.Off) { TickSetup(); return; }
        if (!Active) return;

        Steer = Mathf.Clamp((Axis(map.steer) - map.steerCentre) / (map.steerPerDeg * map.lockDeg), -1f, 1f);
        Throttle = Pedal(Axis(map.throttle), map.throttleRest, map.throttleFull);
        Brake = Pedal(Axis(map.brake), map.brakeRest, map.brakeFull);
        Handbrake = Button(map.handbrake)?.isPressed ?? false;
        ResetPressed = Button(map.reset)?.wasPressedThisFrame ?? false;
        CameraPressed = Button(map.camera)?.wasPressedThisFrame ?? false;
        LookBack = Button(map.lookBack)?.isPressed ?? false;
    }

    /// <summary>0 at rest, 1 fully down, with a little dead travel at both ends so a resting foot or a worn pot never reads as pressed.</summary>
    static float Pedal(float v, float rest, float full) => Mathf.Clamp01((Mathf.InverseLerp(rest, full, v) - 0.03f) / 0.94f);

    bool Moved()
    {
        foreach (var b in buttons) if (b.isPressed) return true;
        for (int i = 0; i < axes.Count; i++) if (Mathf.Abs(Raw(axes[i]) - snapshot[i]) > 0.05f) return true;
        return false;
    }

    // ------------------------------------------------------------ setup
    public void StartSetup()
    {
        if (Device == null) { Log.I("wheel", "setup: no wheel connected"); return; }
        draft = new Mapping { device = Device.displayName, lockDeg = map != null ? map.lockDeg : 180f };
        step = Step.Centre; problem = null;
        Log.I("wheel", $"setup started for '{Name}'");
    }

    public void CancelSetup() { if (step == Step.Off) return; step = Step.Off; Log.I("wheel", "setup cancelled" + (map != null ? ", previous setup kept" : "")); }

    /// <summary>The axis that moved most since the wheel was centred with feet off the pedals, ignoring those already taken.</summary>
    int MostMoved(out float delta)
    {
        int best = -1; delta = 0f;
        var taken = new[] { draft.steer, draft.throttle, draft.brake };
        for (int i = 0; i < axes.Count; i++)
        {
            if (taken.Contains(Rel(axes[i]))) continue;
            float d = Mathf.Abs(Raw(axes[i]) - snapshot[i]);
            if (d > delta) { delta = d; best = i; }
        }
        return best;
    }

    void Refuse(string why) { problem = why; problemAt = Time.unscaledTime; Log.I("wheel", "setup: " + why); }

    void TickSetup()
    {
        var kb = Keyboard.current;
        if (kb != null && kb.escapeKey.wasPressedThisFrame) { CancelSetup(); return; }
        bool enter = kb != null && (kb.enterKey.wasPressedThisFrame || kb.numpadEnterKey.wasPressedThisFrame);
        ButtonControl pressed = buttons.FirstOrDefault(b => b.wasPressedThisFrame);

        if (System.Array.IndexOf(ButtonSteps, step) >= 0)
        {
            if (enter) { Next(); return; }                                    // no button for this one
            if (pressed == null) return;
            string path = Rel(pressed);
            if (new[] { draft.handbrake, draft.reset, draft.camera }.Contains(path)) { Refuse($"{path} is already used"); return; }
            if (step == Step.Handbrake) draft.handbrake = path;
            else if (step == Step.Reset) draft.reset = path;
            else if (step == Step.Camera) draft.camera = path;
            else draft.lookBack = path;
            Next(); return;
        }

        if (pressed == null && !enter) return;
        if (step == Step.Centre) { snapshot = ReadAxes(); Next(); return; }
        int i = MostMoved(out float delta);
        if (step == Step.QuarterTurn)
        {
            if (i < 0 || delta < 0.02f) { Refuse("the wheel did not seem to turn: turn it a quarter turn right, then confirm"); return; }
            draft.steer = Rel(axes[i]); draft.steerCentre = snapshot[i]; draft.steerPerDeg = (Raw(axes[i]) - snapshot[i]) / 90f;
        }
        else
        {
            if (i < 0 || delta < 0.25f) { Refuse("no pedal moved: hold it all the way down, then confirm"); return; }
            if (step == Step.Throttle) { draft.throttle = Rel(axes[i]); draft.throttleRest = snapshot[i]; draft.throttleFull = Raw(axes[i]); }
            else { draft.brake = Rel(axes[i]); draft.brakeRest = snapshot[i]; draft.brakeFull = Raw(axes[i]); }
        }
        Next();
    }

    void Next()
    {
        problem = null;
        if (step != Step.LookBack) { step++; return; }
        map = draft; step = Step.Off; engaged = true;
        Log.I("wheel", $"setup done: steer {map.steer} ({map.steerPerDeg * 90f:+0.000;-0.000} per quarter turn, full lock at {map.lockDeg:F0} deg), throttle {map.throttle} {map.throttleRest:F2}->{map.throttleFull:F2}, brake {map.brake} {map.brakeRest:F2}->{map.brakeFull:F2}, handbrake '{map.handbrake}' reset '{map.reset}' camera '{map.camera}' look back '{map.lookBack}'");
        Save(map);
    }

    string Prompt()
    {
        switch (step)
        {
            case Step.Centre: return "Centre the wheel and take your feet off the pedals.\nThen press any button on the wheel (or Enter).";
            case Step.QuarterTurn: return "Turn the wheel a quarter turn RIGHT (top of the wheel at 3 o'clock) and hold it.\nThen press any button on the wheel (or Enter).";
            case Step.Throttle: return "Press the THROTTLE pedal all the way down and hold it.\nThen press any button on the wheel (or Enter).";
            case Step.Brake: return "Press the BRAKE pedal all the way down and hold it.\nThen press any button on the wheel (or Enter).";
            case Step.Handbrake: return "Press the wheel button you want for the HANDBRAKE.\n(Enter: none)";
            case Step.Reset: return "Press the wheel button you want to RESET the car.\n(Enter: none)";
            case Step.Camera: return "Press the wheel button you want to change CAMERA.\n(Enter: none)";
            case Step.LookBack: return "Press the wheel button you want to LOOK BACK (held).\n(Enter: none)";
            default: return "";
        }
    }

    GUIStyle title, body;
    public void DrawGUI()
    {
        if (step == Step.Off) return;
        if (title == null)
        {
            title = new GUIStyle(GUI.skin.label) { fontSize = 26, fontStyle = FontStyle.Bold, alignment = TextAnchor.UpperCenter }; title.normal.textColor = Color.white;
            body = new GUIStyle(GUI.skin.label) { fontSize = 18, alignment = TextAnchor.UpperCenter, wordWrap = true }; body.normal.textColor = Color.white;
        }
        var r = new Rect(Screen.width * 0.5f - 360f, Screen.height * 0.28f, 720f, 250f);
        GUI.color = new Color(0f, 0f, 0f, 0.75f); GUI.DrawTexture(r, Texture2D.whiteTexture); GUI.color = Color.white;
        GUI.Label(new Rect(r.x, r.y + 14, r.width, 34), $"WHEEL SETUP  {(int)step}/{(int)Step.LookBack}", title);
        GUI.Label(new Rect(r.x + 20, r.y + 52, r.width - 40, 24), Name, body);
        GUI.Label(new Rect(r.x + 20, r.y + 90, r.width - 40, 60), Prompt(), body);
        if (step >= Step.QuarterTurn && step <= Step.Brake)
        {
            int i = MostMoved(out float delta);
            GUI.Label(new Rect(r.x + 20, r.y + 150, r.width - 40, 24), i >= 0 && delta > 0.02f ? $"moving: {Rel(axes[i])}  {Raw(axes[i]):+0.00;-0.00}" : "nothing moving", body);
        }
        if (problem != null && Time.unscaledTime - problemAt < 4f) { GUI.color = new Color(1f, 0.6f, 0.2f); GUI.Label(new Rect(r.x + 20, r.y + 178, r.width - 40, 24), problem, body); GUI.color = Color.white; }
        GUI.Label(new Rect(r.x + 20, r.y + 214, r.width - 40, 24), "Esc: cancel", body);
    }
}

// ------------------------------------------------------------ synthetic wheel for -wheeltest
/// <summary>State of the test wheel: the axes a G29 reports (wheel on X, pedals resting at +1 like the real ones) and a few buttons.</summary>
public struct TestWheelState : IInputStateTypeInfo
{
    public FourCC format => new FourCC('T', 'W', 'H', 'L');
    [InputControl(name = "stick", layout = "Stick", usage = "Primary2DMotion")] public Vector2 stick;
    [InputControl(name = "z", layout = "Axis")] public float z;
    [InputControl(name = "rz", layout = "Axis")] public float rz;
    [InputControl(name = "trigger", layout = "Button", bit = 0, usage = "PrimaryTrigger")]
    [InputControl(name = "button2", layout = "Button", bit = 1)]
    [InputControl(name = "button3", layout = "Button", bit = 2)]
    [InputControl(name = "button4", layout = "Button", bit = 3)]
    public int buttons;
}

[InputControlLayout(stateType = typeof(TestWheelState), displayName = "Test Wheel")]
public class TestWheel : Joystick { }
