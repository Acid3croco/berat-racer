using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;

/// <summary>
/// Scripted vehicle-dynamics tests on a flat asphalt plane. Each scenario drives the car through the normal input API
/// (Throttle / Brake / Steer / Handbrake) and measures the result against real-car targets (Clio-class hot hatch).
/// Run: Berat Racer -batchmode -nographics -phystest -nobcol -notcol
/// </summary>
public static class PhysTest
{
    const float G = 9.81f;
    static CarController car; static WorldBuilder world; static int pass, fail, accStep;
    static readonly List<string> summary = new List<string>();

    static void Check(string what, float value, float lo, float hi, string unit)
    {
        bool ok = value >= lo && value <= hi; if (ok) pass++; else fail++;
        string line = $"{(ok ? "PASS" : "FAIL")}  {what,-44} {value,8:F2} {unit,-5} (target {lo:F1} .. {hi:F1})";
        summary.Add(line); Log.I("phys-test", line);
    }
    static void Info(string s) { summary.Add("      " + s); Log.I("phys-test", s); }

    static void Place(float x, float z, float headingDeg)
    {
        car.Respawn(new Vector3(x, world.FlatY + 0.75f, z), headingDeg);
        car.ResetDynamics();
        car.Throttle = car.Brake = car.Steer = 0; car.Handbrake = false;
    }
    static float Kmh => car.SpeedKmh;
    static float Yaw => car.transform.eulerAngles.y;
    static float Pitch { get { var f = car.transform.forward; return Mathf.Asin(Mathf.Clamp(f.y, -1, 1)) * Mathf.Rad2Deg; } }
    static float Roll { get { var r = car.transform.right; return Mathf.Asin(Mathf.Clamp(r.y, -1, 1)) * Mathf.Rad2Deg; } }
    static float SlipAngleDeg { get { var v = car.Body.linearVelocity; v.y = 0; if (v.magnitude < 3f) return 0; return Vector3.SignedAngle(car.transform.forward, v, Vector3.up); } }

    /// <summary>Simple speed governor used to reach a test speed.</summary>
    static IEnumerator ReachSpeed(float kmh, float steer = 0f, float timeout = 60f)
    {
        float t0 = Time.time;
        while (Time.time - t0 < timeout)
        {
            float e = kmh - Kmh;
            car.Throttle = Mathf.Clamp01(e / 6f); car.Brake = e < -4f ? Mathf.Clamp01(-e / 20f) : 0f; car.Steer = steer;
            if (Mathf.Abs(e) < 1.2f && Time.time - t0 > 0.5f) break;
            yield return new WaitForFixedUpdate();
        }
    }

    public static IEnumerator Run(WorldBuilder w, CarController c)
    {
        world = w; car = c; pass = fail = 0; summary.Clear();
        world.Flat = true; world.FlatY = world.Data.TerrainHeight(0, 0) + 0.02f;
        Time.timeScale = 6f;
        yield return new WaitForSecondsRealtime(0.5f);
        Info("baseline physics tests on flat asphalt; timeScale 6");

        // ---------------------------------------------------------------- 1. acceleration + top speed
        Place(0, 0, 0); yield return new WaitForSeconds(0.5f);
        float t0 = Time.time, t100 = -1, t200 = -1, d100 = 0, tTop = 0; Vector3 p0 = car.transform.position; float lastSpd = 0, peakSlip = 0;
        car.Throttle = 1f;
        while (Time.time - t0 < 130f)
        {
            float t = Time.time - t0;
            if (t100 < 0 && Kmh >= 100f) { t100 = t; d100 = Vector3.Distance(car.transform.position, p0); }
            if (t200 < 0 && Kmh >= 200f) t200 = t;
            peakSlip = Mathf.Max(peakSlip, car.SlipAmount);
            if (++accStep % 25 == 0 && t < 6f) Info($"  launch t={t:F2} {Kmh:F0} km/h gear {car.Gear} {car.Rpm:F0} rpm clutch {car.ClutchEngagement:F2} locked {car.ClutchLocked} Te {car.EngineTorque:F0} Nm kappaRear {car.WheelKappa[2]:F2}/{car.WheelKappa[3]:F2} loadRear {car.WheelLoad[2]:F0}/{car.WheelLoad[3]:F0} accel {car.LongAccel:F1}");
            if (Mathf.Abs(t % 1f) < 0.006f && t < 0f) Info($"  t={t:F0}s  {Kmh:F0} km/h  gear {car.Gear}  {car.Rpm:F0} rpm  accel {car.LongAccel:F2} m/s2  wheel kappa {car.WheelKappa[2]:F2}");
            if (t > 20f && Mathf.Abs(t % 1f) < 0.006f) { if (Mathf.Abs(Kmh - lastSpd) < 0.25f) break; lastSpd = Kmh; }      // settled: <0.25 km/h change per second
            yield return new WaitForFixedUpdate();
        }
        var sp = car.Spec; Info($"car under test: {sp.Name} ({sp.Layout}), {sp.Mass} kg");
        Check("0-100 km/h", t100 < 0 ? 99 : t100, sp.Acc0100Lo, sp.Acc0100Hi, "s");
        if (sp.TopHi > 260f) Check("0-200 km/h", t200 < 0 ? 99 : t200, 8f, 16f, "s"); else Check("0-200 km/h", t200 < 0 ? 99 : t200, 20f, 60f, "s");
        Check("top speed", Kmh, sp.TopLo, sp.TopHi, "km/h");
        Info($"distance to 100 km/h {d100:F0} m");

        // ---------------------------------------------------------------- 2. braking 100 -> 0
        Place(0, 0, 0); yield return ReachSpeed(100f); yield return new WaitForSeconds(0.4f);
        Vector3 pb = car.transform.position; float maxYaw = 0, maxPitch = 0, tB = Time.time; car.Throttle = 0; car.Brake = 1f;
        while (Kmh > 1f && Time.time - tB < 12f)
        {
            maxYaw = Mathf.Max(maxYaw, Mathf.Abs(car.Body.angularVelocity.y) * Mathf.Rad2Deg); maxPitch = Mathf.Max(maxPitch, -Pitch);
            yield return new WaitForFixedUpdate();
        }
        Check("100-0 km/h braking distance", Vector3.Distance(car.transform.position, pb), sp.BrakeLo, sp.BrakeHi, "m");
        Check("braking stability: peak yaw rate", maxYaw, 0f, 8f, "deg/s");
        Check("braking nose dive", maxPitch, 0.3f, 6.0f, "deg");
        car.Brake = 0;

        // ---------------------------------------------------------------- 3. skidpad, R = 30 m: max steady lateral acceleration
        const float R = 30f; float L = car.WheelBase;
        Place(R, 0, 0); car.Body.linearVelocity = car.transform.forward * 11f; yield return new WaitForFixedUpdate();
        float speedT = 40f, best = 0, maxRoll = 0, tS = Time.time; int okFrames = 0, badFrames = 0; float lastGood = 0, prevRad = R;
        while (Time.time - tS < 110f)
        {
            Vector3 pos = car.transform.position; Vector2 rel = new Vector2(pos.x, pos.z); float rad = rel.magnitude;
            // clockwise circle around the origin, starting at (R,0) heading +z
            float err = rad - R;
            float errRate = (rad - prevRad) / Time.fixedDeltaTime; prevRad = rad;
            car.Steer = Mathf.Clamp(-(Mathf.Atan(L / R) + 0.012f * err + 0.030f * errRate) / car.MaxSteerRad(Kmh / 3.6f), -1f, 1f);      // circle is to the left: negative steer; PD on radius error
            speedT += 1.3f * Time.fixedDeltaTime;                                        // +1.3 km/h per second
            float e = speedT - Kmh; car.Throttle = Mathf.Clamp01(e / 6f); car.Brake = e < -6f ? 0.3f : 0f;
            Vector3 v = car.Body.linearVelocity; float aLat = Mathf.Abs(v.magnitude * car.Body.angularVelocity.y) / G;
            if (Mathf.Abs((Time.time - tS) % 2f) < 0.006f) Info($"  skid t={Time.time - tS:F0}s  {Kmh:F0} km/h radius {rad:F1} m  steer {car.Steer:F2}  latG {aLat:F2}  slip s=[{car.WheelSlip[0]:F1},{car.WheelSlip[1]:F1},{car.WheelSlip[2]:F1},{car.WheelSlip[3]:F1}]");
            if (Mathf.Abs(err) < 2.5f) { okFrames++; badFrames = 0; if (aLat > best && Kmh > 50f) { best = aLat; lastGood = Kmh; maxRoll = Mathf.Max(maxRoll, Mathf.Abs(Roll)); } }
            else if (++badFrames > 120) break;                                          // lost the circle for 1.2 s: limit reached
            yield return new WaitForFixedUpdate();
        }
        Check("skidpad max lateral acceleration (R=30 m)", best, sp.SkidLo, sp.SkidHi, "g");
        Info($"limit reached at {lastGood:F0} km/h, roll {maxRoll:F1} deg");
        car.Throttle = car.Brake = car.Steer = 0;

        // ---------------------------------------------------------------- 4. handbrake turn at 70 km/h
        Place(0, 0, 0); yield return ReachSpeed(70f); float yaw0 = Yaw, tH = Time.time, maxBeta = 0, acc = 0, prevY = Yaw, maxRot = 0;
        car.Throttle = 0.3f;
        while (Time.time - tH < 4f)
        {
            float t = Time.time - tH;
            car.Steer = t < 1.0f ? 0.9f : Mathf.Clamp(-SlipAngleDeg / 40f, -1, 1);       // then counter-steer
            car.Handbrake = t > 0.15f && t < 1.0f;
            float y = Yaw; acc += Mathf.DeltaAngle(prevY, y); prevY = y; maxRot = Mathf.Max(maxRot, Mathf.Abs(acc)); maxBeta = Mathf.Max(maxBeta, Mathf.Abs(SlipAngleDeg));
            if (Mathf.Abs((t * 4f) - Mathf.Round(t * 4f)) < 0.02f) Info($"  hb t={t:F2}  yawTotal {acc:F0}  slip {SlipAngleDeg:F0}  {Kmh:F0} km/h  steer {car.Steer:F2}  hand {car.Handbrake}  wheels s=[{car.WheelSlip[0]:F1},{car.WheelSlip[1]:F1},{car.WheelSlip[2]:F1},{car.WheelSlip[3]:F1}]");
            yield return new WaitForFixedUpdate();
        }
        car.Handbrake = false; car.Steer = 0; car.Throttle = 0;
        Check("handbrake turn: peak rotation", maxRot, 90f, 360f, "deg");
        Check("handbrake turn: peak sideslip angle", maxBeta, 35f, 175f, "deg");

        // ---------------------------------------------------------------- 5. hands-off stability at 200 km/h
        Place(0, 0, 0); yield return ReachSpeed(Mathf.Min(200f, car.Spec.TopLo - 15f), 0f, 90f); yield return new WaitForSeconds(0.3f); Info($"  stability test starts at {Kmh:F0} km/h in gear {car.Gear}");
        Vector3 ps = car.transform.position; float hdg0 = Yaw; float maxYawS = 0, maxLatDev = 0; car.Throttle = 0.35f; car.Steer = 0f; float tSt = Time.time;
        while (Time.time - tSt < 4f)
        {
            maxYawS = Mathf.Max(maxYawS, Mathf.Abs(car.Body.angularVelocity.y) * Mathf.Rad2Deg);
            Vector3 d = car.transform.position - ps; maxLatDev = Mathf.Max(maxLatDev, Mathf.Abs(Vector3.Dot(d, Vector3.Cross(Vector3.up, Quaternion.Euler(0, hdg0, 0) * Vector3.forward))));
            yield return new WaitForFixedUpdate();
        }
        if (Kmh < 150f) Info("  (car cannot reach 200 km/h; stability checked at " + Kmh.ToString("F0") + " km/h)");
        Check("200 km/h hands-off: peak yaw rate", maxYawS, 0f, 3f, "deg/s");
        Check("200 km/h hands-off: lateral drift in 4 s", maxLatDev, 0f, 3f, "m");

        // ---------------------------------------------------------------- 6. step steer at 80 km/h
        Place(0, 0, 0); yield return ReachSpeed(80f); float tp = Time.time; float peakYr = 0, yr90 = -1; float finalYr = 0; car.Throttle = 0.2f; car.Steer = 0.10f;
        while (Time.time - tp < 3f)
        {
            float yr = Mathf.Abs(car.Body.angularVelocity.y) * Mathf.Rad2Deg; peakYr = Mathf.Max(peakYr, yr); finalYr = yr;
            
            car.Throttle = Mathf.Clamp01((80f - Kmh) / 6f);
            if (Mathf.Abs(((Time.time - tp) * 10f) - Mathf.Round((Time.time - tp) * 10f)) < 0.05f && Time.time - tp < 1.6f) Info($"  stp t={Time.time - tp:F1} yawRate {car.Body.angularVelocity.y * Mathf.Rad2Deg:F1} slipAngle {SlipAngleDeg:F1} roll {Roll:F1} s=[{car.WheelSlip[0]:F2},{car.WheelSlip[1]:F2},{car.WheelSlip[2]:F2},{car.WheelSlip[3]:F2}] load=[{car.WheelLoad[0]:F0},{car.WheelLoad[1]:F0},{car.WheelLoad[2]:F0},{car.WheelLoad[3]:F0}]");
            yield return new WaitForFixedUpdate();
        }
        float delta = Mathf.Abs(car.SteerAngleDeg) * Mathf.Deg2Rad, kin = 80f / 3.6f * delta / car.WheelBase * Mathf.Rad2Deg;
        Info($"step steer: peak yaw rate {peakYr:F1} deg/s, final {finalYr:F1} deg/s, kinematic {kin:F1} deg/s at {car.SteerAngleDeg:F1} deg road wheel");
        Check("step steer: yaw gain vs kinematic (understeer)", finalYr / Mathf.Max(kin, 0.1f), 0.55f, 0.98f, "x");
        Check("step steer: yaw-rate overshoot", (peakYr / Mathf.Max(finalYr, 0.1f) - 1f) * 100f, -5f, 30f, "%");

        Log.I("phys-test", $"==== {pass} passed, {fail} failed ====");
        System.IO.File.WriteAllText(System.IO.Path.GetFullPath(System.IO.Path.Combine(Application.dataPath, "..", "..", "..", "docs", "physics-" + car.Spec.Name.Replace(" ", "_").Replace("(", "").Replace(")", "") + ".txt")), string.Join("\n", summary) + $"\n==== {pass} passed, {fail} failed ====\n");
        Time.timeScale = 1f; Application.Quit();
    }
}
