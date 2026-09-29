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

    /// <summary>
    /// Road-drive benchmark: the autopilot drives the real road network and we measure what a passenger would feel.
    /// vertical jolt = RMS of high-pass filtered vertical acceleration; hops = physics steps with a wheel airborne on a road;
    /// phantom stops = deceleration above 9 m/s2 with no brake pedal (something invisible was hit).
    /// </summary>
    public static IEnumerator RoadRun(WorldBuilder w, CarController c, float seconds, float kmh)
    {
        world = w; car = c; Time.timeScale = 4f;
        yield return new WaitForSecondsRealtime(0.5f);
        var auto = new Autopilot(world.Data, car) { TargetKmh = kmh };
        float t0 = Time.time, lastVy = car.Body.linearVelocity.y, lp = 0, lpFast = 0, lastSpeed = car.Body.linearVelocity.magnitude, dist = 0;
        double sumSq = 0, harshSq = 0; int n = 0, hops = 0, phantom = 0, steps = 0, respawns = 0, phantomCooldown = 0; float peak = 0; Vector3 lastPos = car.transform.position;
        var events = new List<string>(); var spikes = new List<string>(); int spikeCd = 0, harshCd = 0, envN = 0, envCnt = 0; float envP1 = 0, envP2 = 0; double envSq = 0; var harshList = new List<string>();
        car.Respawned += () => respawns++;
        while (Time.time - t0 < seconds)
        {
            auto.Drive(Time.fixedDeltaTime);
            yield return new WaitForFixedUpdate();
            float dt = Time.fixedDeltaTime; steps++;
            if (steps < 300) { lastVy = car.Body.linearVelocity.y; lastSpeed = car.Body.linearVelocity.magnitude; lastPos = car.transform.position; continue; }   // let it get going
            float vy = car.Body.linearVelocity.y, av = (vy - lastVy) / dt; lastVy = vy;
            var cpos = car.transform.position; if (Mathf.Abs(cpos.x) > WorldData.Half - 100f || Mathf.Abs(cpos.z) > WorldData.Half - 100f) continue;     // ignore the edge of the world
            lp += (av - lp) * (1f - Mathf.Exp(-dt / 0.6f)); lpFast += (av - lpFast) * (1f - Mathf.Exp(-dt / 0.08f));
            double harsh = av - lpFast; harshSq += harsh * harsh;
            { float h0 = car.DebugEnvelope[0]; if (envN >= 2) { float a2 = (h0 - 2 * envP1 + envP2) / (dt * dt); envSq += (double)a2 * a2; envCnt++; } envP2 = envP1; envP1 = h0; envN++; }
            if (Mathf.Abs((float)harsh) > 3.5f && harshCd == 0 && harshList.Count < 14)
            {
                harshCd = 40; var q = car.transform.position; world.GroundHeight(q.x, q.z, q.y, out Surface sfc2);
                world.Roads.Query(q.x, q.z, q.y, out float dk, out float ry, out float rw);
                harshList.Add($"t={Time.time - t0:F0}s harsh {harsh:F1}  at ({q.x:F0},{q.z:F0}) cell=({Mathf.FloorToInt(q.x / 5f)},{Mathf.FloorToInt(q.z / 5f)}) {car.SpeedKmh:F0} km/h roadW {rw:F2} roadY {ry:F2} terrain {world.Data.TerrainHeight(q.x, q.z):F2} car y {q.y:F2} deck {dk:F2}");
            }
            if (harshCd > 0) harshCd--;
            float hp = av - lp; sumSq += hp * hp; n++; peak = Mathf.Max(peak, Mathf.Abs(hp));
            if (Mathf.Abs(hp) > 25f && spikeCd == 0 && spikes.Count < 12)
            {
                spikeCd = 60; var pp = car.transform.position; world.GroundHeight(pp.x, pp.z, pp.y, out Surface sfc);
                spikes.Add($"t={Time.time - t0:F0}s  {hp:F0} m/s2  at ({pp.x:F0},{pp.z:F0}) cell=({Mathf.FloorToInt(pp.x / 5f)},{Mathf.FloorToInt(pp.z / 5f)})  {car.SpeedKmh:F0} km/h  surface {sfc}  wheels {car.WheelsOnGround}/4");
            }
            if (spikeCd > 0) spikeCd--;
            if (car.WheelsOnGround < 4) hops++;
            float sp = car.Body.linearVelocity.magnitude, dec = (lastSpeed - sp) / dt; lastSpeed = sp;
            if (phantomCooldown > 0) phantomCooldown--;
            if (dec > 9f && car.Brake < 0.05f && phantomCooldown == 0 && !car.AbsActive)
            {
                phantom++; phantomCooldown = 100;
                if (events.Count < 8) events.Add($"t={Time.time - t0:F0}s {sp * 3.6f + dec * dt * 3.6f:F0}->{sp * 3.6f:F0} km/h at ({car.transform.position.x:F0},{car.transform.position.z:F0})");
            }
            dist += Vector3.Distance(car.transform.position, lastPos); lastPos = car.transform.position;
        }
        float rms = n > 0 ? Mathf.Sqrt((float)(sumSq / n)) : 0f;
        Log.I("roadtest", $"ROAD BENCHMARK  {seconds:F0}s @ {kmh:F0} km/h target, {dist / 1000f:F1} km driven, avg {dist / seconds * 3.6f:F0} km/h");
        float harshRms = n > 0 ? Mathf.Sqrt((float)(harshSq / n)) : 0f;
        Log.I("roadtest", $"  vertical jolt RMS {rms:F2} m/s2   worst spike {peak:F1} m/s2   HARSHNESS (>2 Hz bumps) RMS {harshRms:F2} m/s2");
        Log.I("roadtest", $"  wheel-hop steps {hops} ({100f * hops / Mathf.Max(n, 1):F2}% of time)");
        Log.I("roadtest", $"  phantom stops (decel > 9 m/s2 without braking): {phantom}    respawns: {respawns}");
        Log.I("roadtest", $"  INPUT: ground-envelope second derivative under front-left wheel, RMS {Mathf.Sqrt((float)(envSq / Mathf.Max(envCnt, 1))):F2} m/s2");
        foreach (var e in events) Log.I("roadtest", "    " + e);
        foreach (var sp in spikes) Log.I("roadtest", "  spike " + sp);
        foreach (var hh in harshList) Log.I("roadtest", "  bump " + hh);
        Time.timeScale = 1f; Application.Quit();
    }

    /// <summary>Drives across the reported bridge (near x=3, z=756) and logs height, wheel contact and vertical acceleration.</summary>
    public static IEnumerator BridgeRun(WorldBuilder w, CarController c)
    {
        world = w; car = c; Time.timeScale = 2f;
        yield return new WaitForSecondsRealtime(0.5f);
        yield return world.LoadAround(new Vector3(3f, 0, 756f), 900f, 1500f);
        RoadData bridge = null; float bd = 1e9f;                // the bridge deck nearest the reported spot
        foreach (var rd in world.Data.Roads) { if (!rd.bridge) continue; var m = rd.XZ[rd.XZ.Length / 2]; float d = Vector2.Distance(m, new Vector2(3f, 756f)); if (d < bd) { bd = d; bridge = rd; } }
        if (bridge == null) { Log.I("bridgetest", "no bridge near (3,756)"); Application.Quit(); yield break; }
        Log.I("bridgetest", $"bridge piece {bd:F0} m from the reported spot, {bridge.XZ.Length} points");
        var route = Chain(world.Data, bridge, 3);             // the bridge and both approaches, in driving order
        Vector2 p0 = route[0], p1 = route[Mathf.Min(4, route.Count - 1)];
        float hdg = Mathf.Atan2(p1.x - p0.x, p1.y - p0.y) * Mathf.Rad2Deg;
        float gy = world.GroundHeight(p0.x, p0.y, 300f, out _);
        car.Respawn(new Vector3(p0.x, gy + 0.8f, p0.y), hdg);
        var auto = new Autopilot(world.Data, car) { TargetKmh = 50f };
        auto.FollowPolyline(route);
        float t0 = Time.time, lastVy = 0, maxA = 0; int minWheels = 4; bool dumped = false;
        while (Time.time - t0 < 14f)
        {
            auto.Drive(Time.fixedDeltaTime); yield return new WaitForFixedUpdate();
            float av = (car.Body.linearVelocity.y - lastVy) / Time.fixedDeltaTime; lastVy = car.Body.linearVelocity.y;
            if (Time.time - t0 > 1f) maxA = Mathf.Max(maxA, Mathf.Abs(av));
            minWheels = Mathf.Min(minWheels, car.WheelsOnGround);
            if (!dumped && Time.time - t0 > 6f && car.SpeedKmh < 4f)
            {   // stalled: say what the car is touching
                dumped = true; var cp = car.transform.position;
                foreach (var col in Physics.OverlapSphere(cp + Vector3.up * 0.5f, 3.5f)) Log.I("bridgetest", $"  stalled at ({cp.x:F1},{cp.y:F1},{cp.z:F1}): touching '{col.transform.parent?.name}/{col.name}' {col.GetType().Name} bounds {col.bounds.min} .. {col.bounds.max}");
            }
            if (Mathf.Abs(((Time.time - t0) * 2f) - Mathf.Round((Time.time - t0) * 2f)) < 0.011f)
            {
                var p = car.transform.position; float g = world.GroundHeight(p.x, p.z, p.y, out Surface sf); world.Roads.Query(p.x, p.z, p.y, out float deck);
                Log.I("bridgetest", $"t={Time.time - t0:F1}s  pos=({p.x:F1},{p.z:F1})  {car.SpeedKmh:F0} km/h  car y {p.y:F2}  ground {g:F2}  deck {(float.IsNaN(deck) ? "-" : deck.ToString("F2"))}  wheels {car.WheelsOnGround}/4");
            }
        }
        Log.I("bridgetest", $"peak vertical acceleration {maxA:F1} m/s2, fewest wheels on ground {minWheels}");
        Application.Quit();
    }

    /// <summary>The seed road piece plus up to `hops` connected pieces on each side (joined end to end), in driving order.</summary>
    static List<Vector2> Chain(WorldData data, RoadData seed, int hops)
    {
        List<Vector2> Grow(List<Vector2> pts)
        {
            var used = new HashSet<RoadData> { seed };
            for (int h = 0; h < hops; h++)
            {
                Vector2 end = pts[pts.Count - 1]; RoadData best = null; bool rev = false; float bestD = 4f;
                foreach (var r in data.Roads)
                {
                    if (used.Contains(r) || r.XZ.Length < 2) continue;
                    float d0 = Vector2.Distance(r.XZ[0], end), d1 = Vector2.Distance(r.XZ[r.XZ.Length - 1], end);
                    if (d0 < bestD) { bestD = d0; best = r; rev = false; } else if (d1 < bestD) { bestD = d1; best = r; rev = true; }
                }
                if (best == null) break;
                used.Add(best); var np = new List<Vector2>(best.XZ); if (rev) np.Reverse(); pts.AddRange(np);
            }
            return pts;
        }
        var fwd = Grow(new List<Vector2>(seed.XZ));
        var back = Grow(new List<Vector2>(System.Linq.Enumerable.Reverse(seed.XZ)));
        back.Reverse();
        var seedPts = seed.XZ.Length;
        // back = [..., seed] in driving order, fwd = [seed, ...]; join them on the seed
        var route = new List<Vector2>(back.GetRange(0, back.Count - seedPts)); route.AddRange(fwd);
        return route;
    }

    /// <summary>Whole-car collision: drop the car on its side / roof onto flat ground and onto a slope and check no hull point sinks in.</summary>
    public static IEnumerator HullRun(WorldBuilder w, CarController c)
    {
        world = w; car = c; Time.timeScale = 2f; int fails = 0;
        yield return new WaitForSecondsRealtime(0.5f);
        world.Flat = true; world.FlatY = world.Data.TerrainHeight(0, 0) + 0.02f;
        foreach (var (name, roll, pitch) in new[] { ("upright drop from 1.5 m", 0f, 0f), ("on its side", 90f, 0f), ("upside down", 180f, 0f), ("nose-first", 0f, 75f), ("tail-first", 0f, -75f) })
        {
            car.Respawn(new Vector3(0, world.FlatY + 1.5f + 2.6f * Mathf.Abs(Mathf.Sin(pitch * Mathf.Deg2Rad)), 0), 0f); car.ResetDynamics();      // start with nothing already inside the ground
            car.Body.rotation = Quaternion.Euler(pitch, 0, roll); car.transform.rotation = Quaternion.Euler(pitch, 0, roll);
            Physics.SyncTransforms();
            float worst = 0f, t0 = Time.time;
            while (Time.time - t0 < 2.2f) { yield return new WaitForFixedUpdate(); worst = Mathf.Max(worst, car.MaxHullPenetration()); }
            bool ok = worst < 0.12f; if (!ok) fails++;
            Log.I("hulltest", $"{(ok ? "PASS" : "FAIL")}  {name,-24} deepest hull penetration {worst * 100f:F1} cm, settled speed {car.SpeedKmh:F1} km/h");
        }
        Log.I("hulltest", fails == 0 ? "ALL PASS" : fails + " FAILED");
        Time.timeScale = 1f; Application.Quit();
    }

    /// <summary>Checks the coordinate grid maths and the parser for every format a bug report might use.</summary>
    public static void GridRun()
    {
        int fails = 0;
        void Eq(string what, bool ok, string got) { if (!ok) fails++; Log.I("gridtest", $"{(ok ? "PASS" : "FAIL")}  {what}: {got}"); }
        var c = Grid.CellOf(3f, 756f); Eq("cell of (3, 756)", c.x == 0 && c.y == 151, c.ToString());
        c = Grid.CellOf(-0.1f, -4.9f); Eq("cell of (-0.1, -4.9)", c.x == -1 && c.y == -1, c.ToString());
        foreach (var (text, ex, ez) in new[] { ("x=3 z=756", 3f, 756f), ("x3 z 756", 3f, 756f), ("x: -12,5  z: 40", -12.5f, 40f), ("cell (0, 151)", 2.5f, 757.5f), ("3 756", 3f, 756f), ("(3, 756)", 3f, 756f), ("BERAT x=55.6 z=-33.6 y=245.3 cell=(11, -7) heading=31", 55.6f, -33.6f) })
        {
            bool ok = Grid.TryParse(text, out float x, out float z) && Mathf.Abs(x - ex) < 0.01f && Mathf.Abs(z - ez) < 0.01f; Eq($"parse \"{text}\"", ok, $"{x}, {z}");
        }
        Eq("reject garbage", !Grid.TryParse("hello world", out _, out _), "-");
        Log.I("gridtest", fails == 0 ? "ALL PASS" : fails + " FAILED");
    }
}
