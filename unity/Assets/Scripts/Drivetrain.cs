using UnityEngine;

/// <summary>
/// Engine + clutch + 6-speed automatic + final drive. 150 kW turbo four (peak 292 Nm), idle 900, redline 7000 rpm.
/// The clutch has three regimes: open (shifting / idle), slipping (launch: torque is limited by clutch capacity) and locked
/// (engine speed follows the wheels and its inertia is reflected into them, which keeps the coupled system stable at 100 Hz).
/// </summary>
public class Drivetrain
{
    public const float RadToRpm = 60f / (2f * Mathf.PI), RpmToRad = 2f * Mathf.PI / 60f;
    public float Idle = 900f, Redline = 7000f, LimiterOn = 7050f, LimiterOff = 7250f, Ie = 0.16f, FinalDrive = 3.9f, ReverseRatio = 3.4f, Efficiency = 0.92f, ClutchMax = 700f;
    float[] Ratios = { 3.615f, 2.238f, 1.520f, 1.156f, 0.911f, 0.756f };
    float[] TqRpm = { 0, 900, 1500, 2000, 3000, 4500, 5500, 6500, 7000, 7600 };
    float[] TqNm = { 80, 120, 175, 235, 285, 292, 275, 236, 200, 120 };
    public int Gears => Ratios.Length;

    public Drivetrain(CarSpec spec)
    {
        Idle = spec.Idle; Redline = spec.Redline; LimiterOn = spec.Redline + 50f; LimiterOff = spec.Redline + 250f; Ie = spec.EngineInertia;
        FinalDrive = spec.FinalDrive; ReverseRatio = spec.ReverseRatio; Efficiency = spec.Efficiency; Ratios = spec.Ratios; TqRpm = spec.TqRpm; TqNm = spec.TqNm;
        ClutchMax = Mathf.Max(spec.TqNm) * 1.7f;
        OmegaE = Idle * RpmToRad;
    }

    public float OmegaE;
    public int Gear = 1;                           // -1 reverse, 0 neutral, 1..N
    public float Clutch, Load, TorqueE;
    public bool Locked, Limiting;
    float shiftT, holdT;

    public float Rpm => OmegaE * RadToRpm;
    public float Ratio => Gear == 0 ? 0f : (Gear < 0 ? -ReverseRatio : Ratios[Gear - 1]) * FinalDrive;
    public bool Shifting => shiftT > 0f;

    public void Reset() { OmegaE = Idle * RpmToRad; Gear = 1; Clutch = 0f; shiftT = holdT = 0f; Locked = false; Limiting = false; TorqueE = 0f; }
    public void SetReverse(bool on)
    {
        if (on && Gear >= 0) { Gear = -1; shiftT = 0.25f; holdT = 0.5f; }
        else if (!on && Gear < 0) { Gear = 1; shiftT = 0.25f; holdT = 0.5f; }
    }

    float Curve(float rpm)
    {
        if (rpm <= TqRpm[0]) return TqNm[0];
        for (int i = 1; i < TqRpm.Length; i++)
            if (rpm <= TqRpm[i]) return Mathf.Lerp(TqNm[i - 1], TqNm[i], (rpm - TqRpm[i - 1]) / (TqRpm[i] - TqRpm[i - 1]));
        return TqNm[TqNm.Length - 1];
    }

    /// <summary>Engine torque at the crank (Nm), including fuel cut at the limiter and pumping / friction braking when the throttle is closed.</summary>
    float EngineTorque(float thr)
    {
        float rpm = Rpm;
        if (Limiting) { if (rpm < LimiterOn - 150f) Limiting = false; } else if (rpm > LimiterOff) Limiting = true;
        float idleThr = Mathf.Clamp((Idle + 100f - rpm) / 200f, 0f, 0.3f);
        float t = Mathf.Max(thr, idleThr);
        float fric = 16f + 0.0085f * rpm;
        float drive = Limiting ? 0f : Curve(rpm) * t;
        return drive - fric * (1f - t);
    }

    /// <param name="shiftThrottle">driver throttle (drives the gearbox decisions)</param>
    /// <param name="throttle">throttle actually reaching the engine (after traction control)</param>
    /// <param name="wheelOmega">mean angular speed of the driven wheels (rad/s, negative when rolling backwards)</param>
    /// <param name="extTorque">resisting torque on the driven axle from the tyres and brakes over the last step (Nm, both wheels)</param>
    /// <param name="axleInertia">inertia of the two driven wheels together (kg m2)</param>
    /// <param name="torquePerWheel">drive torque to each driven wheel (Nm)</param>
    public void Step(float dt, float shiftThrottle, float throttle, float wheelOmega, float extTorque, float axleInertia, float speedMs, out float torquePerWheel)
    {
        Load = shiftThrottle;
        float G = Ratio, wG = G * wheelOmega, rpmW = wG * RadToRpm, rpm = Rpm;

        // ---- automatic gearbox (decisions use the driver's pedal, not the traction-controlled one)
        holdT -= dt; if (shiftT > 0f) shiftT -= dt;
        if (Gear >= 1 && shiftT <= 0f && holdT <= 0f)
        {
            float rs = Redline / 7000f;
            float up = (3000f + 3300f * shiftThrottle) * rs, down = (1500f + 1100f * shiftThrottle) * rs;
            if (rpm > up && Gear < Ratios.Length && speedMs > 2.5f && rpmW * Ratios[Gear] / Ratios[Gear - 1] > 2200f * rs) StartShift(+1);
            else if (Gear > 1 && (rpm < down || (shiftThrottle > 0.85f && rpm < 3000f * rs)) && rpmW * Ratios[Gear - 2] / Ratios[Gear - 1] < 0.92f * Redline) StartShift(-1);
        }

        // ---- clutch capacity: launch on engine rpm, stay engaged on wheel-side rpm, open while shifting
        float cLaunch = Smooth(Idle + 300f, Idle + 1700f, rpm) * (shiftThrottle > 0.03f ? 1f : 0f);
        float cRoll = Smooth(Idle - 200f, Idle + 200f, rpmW);
        float target = Gear == 0 ? 0f : Mathf.Max(cLaunch, cRoll);
        if (shiftT > 0.13f) target = 0f;
        Clutch += (target - Clutch) * (1f - Mathf.Exp(-30f * dt));

        float Te = EngineTorque(throttle); TorqueE = Te;
        float cap = Gear == 0 ? 0f : Clutch * ClutchMax;
        torquePerWheel = 0f;
        if (cap < 0.5f) { OmegaE = Mathf.Max(OmegaE + dt * Te / Ie, 60f); Locked = false; return; }

        // ---- solve engine + driven axle together: the clutch torque that makes their speeds equal at the end of the step,
        //      then limit it to the clutch capacity (locked if it fits, slipping at capacity if it does not)
        float effG = G * Efficiency;
        float Tlock = ((OmegaE - wG) / dt + Te / Ie + G * extTorque / axleInertia) / (1f / Ie + G * effG / axleInertia);
        Locked = Mathf.Abs(Tlock) <= cap;
        float Tc = Locked ? Tlock : Mathf.Sign(Tlock) * cap;
        OmegaE = Mathf.Max(OmegaE + dt * (Te - Tc) / Ie, 60f);
        torquePerWheel = 0.5f * Tc * effG;
    }

    void StartShift(int dir) { Gear += dir; shiftT = 0.28f; holdT = 0.75f; Locked = false; }
    static float Smooth(float a, float b, float x) { float t = Mathf.Clamp01((x - a) / (b - a)); return t * t * (3f - 2f * t); }
}
