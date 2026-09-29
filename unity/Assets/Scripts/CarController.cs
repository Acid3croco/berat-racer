using System.Collections.Generic;
using UnityEngine;

public enum Assist { Off, Sport, Full, Arcade }

/// <summary>
/// Vehicle dynamics for a Clio-class hot hatch (1250 kg, RWD, 150 kW turbo, 6-speed automatic).
///  - chassis: Rigidbody with measured mass distribution and inertia tensor
///  - suspension: four spring/damper corners + anti-roll bars + progressive bump stops, ground contact found by rolling a wheel
///    of real radius over the LiDAR heightfield (three samples per wheel), so kerbs and crests do not jolt
///  - wheels: real angular velocity (I = 1.5 kg m2), slip ratio and slip angle from the contact-patch velocity, semi-implicit and sub-stepped
///  - tyres: combined-slip friction curve with load sensitivity and a lateral relaxation length (Tyre.cs)
///  - drivetrain: engine torque curve, clutch, automatic gearbox, open / limited-slip differential (Drivetrain.cs)
///  - aids: ABS, traction control, slip-angle-limited steering (Sport / Full), Ackermann geometry, aero drag and downforce
/// </summary>
[RequireComponent(typeof(Rigidbody))]
public class CarController : MonoBehaviour
{
    public WorldBuilder World;
    public Assist AssistMode = Assist.Arcade;

    // ------------------------------------------------------------------ specification (filled from CarSpec in Init)
    public CarSpec Spec { get; private set; }
    public float Mass, WheelR, WheelBase, TrackWidth, FrontZ, RearZ;
    const float WheelI = 1.5f, DStatic = 0.40f, DroopTravel = 0.12f, DMax = DStatic + DroopTravel, DMin = 0.14f;
    const float BumpStopK = 160000f, BumpTravel = 0.10f;
    const float Mu0 = 1.20f, LoadSens = 0.10f, RelaxLen = 0.35f, MuYRatio = 0.96f, Rho = 1.2f;
    float[] springK = new float[4], bumpC = new float[4], reboundC = new float[4], staticLoad = new float[4];
    float arbFront, arbRear, fzNom, cdA, clA, clFront, brakeFront, brakeRear, handbrakeTq, muScale, lsdPreload, muFrontScale = 1f, muRearScale = 1f;
    float xStop;

    // ------------------------------------------------------------------ inputs (player, autopilot or test harness)
    [HideInInspector] public float Throttle, Brake, Steer;
    [HideInInspector] public bool Handbrake;

    // ------------------------------------------------------------------ outputs
    public float SpeedKmh => Body.linearVelocity.magnitude * 3.6f;
    public float ForwardSpeed => Vector3.Dot(Body.linearVelocity, transform.forward);
    public Rigidbody Body { get; private set; }
    public int WheelsOnGround { get; set; }
    /// <summary>0..1 how hard the tyres are sliding (drives squeal and camera shake).</summary>
    public float SlipAmount { get; set; }
    public float LongAccel { get; set; }
    public float Rpm => Model != null ? Model.Rpm : drivetrain.Rpm;
    public int Gear => Model != null ? Model.Gear : drivetrain.Gear;
    public float EngineLoad => Model != null ? Model.Load : drivetrain.Load;
    public bool Shifting => Model != null ? Model.Shifting : drivetrain.Shifting;
    public float ClutchEngagement => drivetrain.Clutch;
    public bool ClutchLocked => drivetrain.Locked;
    public float EngineTorque => drivetrain.TorqueE;
    public float SteerAngleDeg => steerCentre * Mathf.Rad2Deg;
    public readonly float[] WheelFx = new float[4];             // 0..1 smoke / skid intensity
    public readonly float[] WheelSlip = new float[4];           // combined slip s (1 = peak grip)
    public readonly float[] WheelKappa = new float[4];          // slip ratio
    public readonly float[] WheelLoad = new float[4];           // N
    public readonly float[] DebugEnvelope = new float[4];       // ground envelope height under each wheel (test instrumentation)
    public readonly float[] DebugComp = new float[4];
    public readonly Vector3[] WheelPoint = new Vector3[4];
    public readonly Surface[] SurfaceUnderWheel = new Surface[4];
    public Surface CurrentSurface { get; set; }
    // ---- pluggable driving model (null = the built-in realistic simulation below)
    public DrivingModel Model { get; private set; }
    public int ModelIndex { get; private set; }
    public Transform[] WheelVisuals { get; private set; }
    public void SetModel(int index)
    {
        index = ((index % DrivingModels.Count) + DrivingModels.Count) % DrivingModels.Count;
        Model?.Detach();
        Model = DrivingModels.Create(index); ModelIndex = index;
        if (Model != null) { Model.Attach(this); Model.OnReset(); }
        ResetDynamics();
        Log.I("car", $"driving model: {DrivingModels.Names[index]}");
    }
    bool waitingForGround; int insideCheck; public static bool NoBuildingEscape;
    public event System.Action Respawned;
    public event System.Action<float> Impact;
    public bool AbsActive { get; private set; }
    public bool TcsActive { get; private set; }

    class Wheel
    {
        public Vector3 mount; public bool front, driven; public Transform visual;
        public float omega, spin, steer, x, xPrev, d = DStatic, fy, kappa, s, fz, fxAvg;
        public bool grounded; public Vector3 contact, n, f, r; public float vx, vy; public Surface surf; public float lastGround;
    }
    Wheel[] w;
    Drivetrain drivetrain;
    int dA, dB;                                   // indices of the two driven wheels (front pair for FWD, rear pair for RWD)
    float steerSm, steerCentre, driveSm, brakeSm, prevFwdSpeed, flipTimer, tcsCut;
    Vector3[] belly; readonly float[] prevBrakeTq = new float[4];

    public float MaxSteerRad(float v) => Spec.MaxSteerDeg * Mathf.Deg2Rad / Mathf.Pow(1f + (v / 22f) * (v / 22f), 0.75f);

    // ------------------------------------------------------------------ setup
    public void Init(WorldBuilder world, Transform[] wheelVisuals, CarSpec spec)
    {
        World = world; Spec = spec; WheelVisuals = wheelVisuals;
        Mass = spec.Mass; WheelR = spec.WheelR; WheelBase = spec.Wheelbase; TrackWidth = 0.5f * (spec.TrackF + spec.TrackR); FrontZ = spec.A; RearZ = -spec.B;
        Body = GetComponent<Rigidbody>();
        var box = gameObject.AddComponent<BoxCollider>();
        box.enabled = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-nocarcol") < 0;
        float bodyMid = 0.5f * (spec.ZFront + spec.ZRear), bodyLen = spec.ZFront - spec.ZRear, clearance = 0.16f;
        float yBottom = spec.GroundY + clearance, yTop = spec.GroundY + spec.Height * 0.72f;      // collision box covers the body up to the belt line
        box.center = new Vector3(0, 0.5f * (yTop + yBottom), bodyMid); box.size = new Vector3(spec.Width * 0.98f, yTop - yBottom, bodyLen * 0.98f);
        box.material = new PhysicsMaterial { dynamicFriction = 0.1f, staticFriction = 0.1f, bounciness = 0.05f };
        Body.mass = Mass; Body.centerOfMass = spec.CentreOfMass; Body.inertiaTensor = spec.InertiaTensor; Body.inertiaTensorRotation = Quaternion.identity;
        Body.linearDamping = 0f; Body.angularDamping = 0.02f;
        Body.interpolation = RigidbodyInterpolation.Interpolate;
        Body.collisionDetectionMode = CollisionDetectionMode.ContinuousDynamic;
        Body.solverIterations = 8;

        // suspension rates from the target ride frequency of the sprung mass on each axle; static corner loads keep the ride height equal for every car
        float g = 9.81f;
        for (int i = 0; i < 4; i++)
        {
            float share = i < 2 ? spec.FrontWeight : 1f - spec.FrontWeight;
            float mCorner = Mass * share * 0.5f * 0.92f;                                   // ~8% of the weight is unsprung
            float f = i < 2 ? spec.RideFreqF : spec.RideFreqR;
            springK[i] = mCorner * Mathf.Pow(2f * Mathf.PI * f, 2f);
            float cCrit = 2f * Mathf.Sqrt(springK[i] * mCorner);
            bumpC[i] = spec.DampBumpRatio * cCrit; reboundC[i] = spec.DampReboundRatio * cCrit;
            staticLoad[i] = Mass * g * share * 0.5f;
        }
        arbFront = spec.ArbF; arbRear = spec.ArbR; fzNom = Mass * g * 0.25f; cdA = spec.CdA; clA = spec.ClA; clFront = spec.ClFront;
        muFrontScale = spec.MuFrontScale; muRearScale = spec.MuRearScale; brakeFront = spec.BrakeF; brakeRear = spec.BrakeR; handbrakeTq = spec.HandbrakeTq; muScale = spec.MuScale; lsdPreload = spec.LsdPreload;
        xStop = DroopTravel + BumpTravel;

        drivetrain = new Drivetrain(spec);
        dA = spec.Layout == DriveLayout.FWD ? 0 : 2; dB = dA + 1;
        w = new Wheel[4];
        for (int i = 0; i < 4; i++) w[i] = new Wheel { mount = spec.Mount(i), front = i < 2, driven = i == dA || i == dB, visual = wheelVisuals[i] };
        // hull collision points: skid plate, sills, bumpers and roof corners, so the WHOLE car collides with the ground (crests, ditches, kerbs, rolling over)
        var hp = new List<Vector3>();
        float wx = spec.Width * 0.5f, yLow = spec.GroundY + clearance * 0.8f, yMid = spec.GroundY + 0.30f, yNose = spec.GroundY + 0.40f, yRoof = spec.GroundY + spec.Height - 0.03f;
        foreach (float zz in new[] { spec.ZFront - 0.25f, 0f, spec.ZRear + 0.25f }) { hp.Add(new Vector3(-wx * 0.78f, yLow, zz)); hp.Add(new Vector3(wx * 0.78f, yLow, zz)); }
        hp.Add(new Vector3(0f, yLow, 0f));
        hp.Add(new Vector3(-wx * 0.98f, yMid, 0.9f)); hp.Add(new Vector3(wx * 0.98f, yMid, 0.9f)); hp.Add(new Vector3(-wx * 0.98f, yMid, -0.9f)); hp.Add(new Vector3(wx * 0.98f, yMid, -0.9f));   // sills
        hp.Add(new Vector3(-wx * 0.85f, yNose, spec.ZFront)); hp.Add(new Vector3(wx * 0.85f, yNose, spec.ZFront)); hp.Add(new Vector3(0f, yNose, spec.ZFront));                                    // front bumper
        hp.Add(new Vector3(-wx * 0.85f, yNose, spec.ZRear)); hp.Add(new Vector3(wx * 0.85f, yNose, spec.ZRear)); hp.Add(new Vector3(0f, yNose, spec.ZRear));                                       // rear bumper
        foreach (float zz in new[] { spec.A - 0.9f, -spec.B + 0.7f }) { hp.Add(new Vector3(-wx * 0.7f, yRoof, zz)); hp.Add(new Vector3(wx * 0.7f, yRoof, zz)); }                                    // roof corners
        belly = hp.ToArray();
        ResetDynamics();
        Log.I("car", $"{spec.Name}: {Mass} kg, {spec.Layout}, wheelbase {WheelBase:F2} m, front weight {spec.FrontWeight * 100:F0}%, {spec.Idle}-{spec.Redline} rpm, {drivetrain.Gears}-speed auto, ride {spec.RideFreqF:F2}/{spec.RideFreqR:F2} Hz");
    }

    /// <summary>Deepest any hull collision point is below the ground right now (m); 0 when nothing penetrates. Test instrumentation.</summary>
    public float MaxHullPenetration()
    {
        float worst = 0f;
        foreach (var lp in belly) { Vector3 p = transform.TransformPoint(lp); worst = Mathf.Max(worst, World.GroundHeight(p.x, p.z, p.y + 0.6f, out _) - p.y); }
        return worst;
    }

    /// <summary>Clears wheel speeds, filters and drivetrain so a manoeuvre starts from rest.</summary>
    public void ResetDynamics()
    {
        if (w == null) return;
        for (int i = 0; i < 4; i++)
        {
            var q = w[i]; q.omega = 0; q.fy = 0; q.steer = 0; q.d = DStatic; q.x = q.xPrev = DroopTravel; q.kappa = q.s = 0; q.grounded = false;
            WheelFx[i] = WheelSlip[i] = WheelKappa[i] = 0;
        }
        drivetrain.Reset(); steerSm = steerCentre = driveSm = brakeSm = 0; SlipAmount = LongAccel = 0; prevFwdSpeed = 0; tcsCut = 0;
        Model?.OnReset();
    }

    // ------------------------------------------------------------------ physics step
    void FixedUpdate()
    {
        if (World == null || !World.Ready || w == null) return;
        float dt = Time.fixedDeltaTime;
        if (!World.Flat && !World.FootprintLoaded(transform.position))
        {   // streaming has not delivered the terrain under the car (or it is past the map edge): hold it in place rather than let it fall through
            if (!waitingForGround) { waitingForGround = true; Log.I("car", $"waiting for terrain at ({transform.position.x:F0},{transform.position.z:F0})"); }
            Body.linearVelocity = Vector3.zero; Body.angularVelocity = Vector3.zero; Body.AddForce(-Physics.gravity, ForceMode.Acceleration);
            return;
        }
        waitingForGround = false;
        if (!World.Flat && !NoBuildingEscape && (++insideCheck & 7) == 0 && World.InsideBuilding(transform.position, out Vector3 outside))
        {   // trapped in a building (map teleport onto a roof, tunnelling at speed): step out through the nearest wall
            float gy = World.GroundHeight(outside.x, outside.z, outside.y, out _);
            Log.I("car", $"inside a building at ({transform.position.x:F1},{transform.position.z:F1}) -> moved out to ({outside.x:F1},{outside.z:F1})");
            Respawn(new Vector3(outside.x, gy + 0.8f, outside.z));
            return;
        }
        Vector3 vel = Body.linearVelocity; float speed = vel.magnitude, fwdSpeed = ForwardSpeed;
        LongAccel = Mathf.Lerp(LongAccel, Mathf.Clamp((fwdSpeed - prevFwdSpeed) / dt, -25f, 25f), 0.03f); prevFwdSpeed = fwdSpeed;

        float floor = World.Flat ? World.FlatY : World.Data.TerrainHeight(transform.position.x, transform.position.z);
        if (transform.position.y < floor - 15f || transform.position.y > floor + 400f || float.IsNaN(transform.position.y))
        {
            Log.I("car", $"out of world (y={transform.position.y:F1}, floor={floor:F1}) -> safety respawn");
            Respawn(new Vector3(float.IsNaN(transform.position.x) ? 0 : transform.position.x, floor + 1.2f, float.IsNaN(transform.position.z) ? 0 : transform.position.z));
            return;
        }
        if (Model != null) { Model.FixedStep(dt); return; }                                                  // another driving model owns the step
        Vector3 up = transform.up;
        bool upright = up.y > 0.25f;
        if (!upright && speed < 3f) { flipTimer += dt; if (flipTimer > 2.5f) { Respawn(transform.position + Vector3.up * 1f); return; } } else flipTimer = 0f;

        ReadPedals(dt, fwdSpeed, out float drivePedal, out float brakePedal);
        UpdateSteering(dt, speed);

        // ---- 1. suspension geometry and contact for every corner
        int grounded = 0;
        for (int i = 0; i < 4; i++)
        {
            if (upright) Geometry(w[i], up, dt); else w[i].grounded = false;
            if (w[i].grounded) grounded++;
        }
        WheelsOnGround = grounded;
        CurrentSurface = w[0].surf;
        int wet = 0; for (int i = 0; i < 4; i++) if (w[i].grounded && w[i].surf == Surface.Water) wet++;
        if (wet > 0)
        {   // wading: hydrodynamic drag grows with depth and with the square of speed
            float depth = Mathf.Clamp(World.WaterDepth(transform.position.x, transform.position.z), 0f, 0.7f);
            Body.AddForce(-vel * speed * 26f * depth * (wet / 4f), ForceMode.Force);
        }
        // ---- 2. suspension forces (spring + damper + anti-roll + bump stop)
        for (int i = 0; i < 4; i++)
        {
            var q = w[i];
            if (!q.grounded) { q.fz = 0; q.xPrev = 0; WheelLoad[i] = 0; continue; }
            var o = w[i ^ 1];
            float xo = o.grounded ? o.x : 0f;
            float xdot = Mathf.Clamp((q.x - q.xPrev) / dt, -4f, 4f); q.xPrev = q.x;
            float F = staticLoad[i] + springK[i] * (q.x - DroopTravel) + (i < 2 ? arbFront : arbRear) * (q.x - xo) + (xdot > 0 ? bumpC[i] : reboundC[i]) * xdot;
            if (q.x > xStop) { float e = q.x - xStop; F += BumpStopK * e + 4e6f * e * e; }
            q.fz = Mathf.Max(0f, F); WheelLoad[i] = q.fz;
            Body.AddForceAtPosition(up * q.fz, transform.TransformPoint(q.mount));
        }

        // ---- 3. drivetrain and brakes
        float omegaDriven = 0.5f * (w[dA].omega + w[dB].omega);
        float maxKappaDriven = Mathf.Max(w[dA].kappa, w[dB].kappa);
        tcsCut = 0f; TcsActive = false;
        if (AssistMode != Assist.Off && drivePedal > 0.05f && maxKappaDriven > 0.22f) { tcsCut = Mathf.Clamp((maxKappaDriven - 0.22f) * 6f, 0f, 0.85f); TcsActive = true; }
        if (AssistMode == Assist.Arcade && drivePedal > 0.05f && speed > 6f)
        {   // sideways: back off the power until the car is pointing where it travels again
            Vector3 lvA = transform.InverseTransformDirection(vel); float betaA = Mathf.Abs(Mathf.Atan2(lvA.x, Mathf.Max(lvA.z, 0.5f)));
            float cutA = Mathf.Clamp01((betaA - 0.22f) / 0.20f) * 0.8f;
            if (cutA > tcsCut) { tcsCut = cutA; TcsActive = true; }
        }
        // resisting torque on the driven axle from last step's tyre forces and brakes (lets the drivetrain solve exactly)
        float extT = 0f;
        for (int k = 0; k < 2; k++) { var dw0 = w[k == 0 ? dA : dB]; extT += WheelR * dw0.fxAvg + prevBrakeTq[k == 0 ? dA : dB] * Mathf.Sign(dw0.omega) * (Mathf.Abs(dw0.omega) > 0.5f ? 1f : 0f); }
        drivetrain.Step(dt, drivePedal, drivePedal * (1f - tcsCut), omegaDriven, extT, 2f * WheelI, speed, out float tqWheel);

        float tqL = tqWheel, tqR = tqWheel;
        if (w[dA].grounded || w[dB].grounded)
        {   // limited-slip: preload + speed-difference lock, transfers torque from the faster wheel to the slower one
            float dw = w[dA].omega - w[dB].omega;
            float lockT = Mathf.Clamp(lsdPreload + 22f * Mathf.Abs(dw), 0f, lsdPreload + 0.55f * Mathf.Abs(tqWheel));
            float transfer = Mathf.Sign(dw) * lockT;
            tqL -= transfer; tqR += transfer;
        }
        float[] brakeTq = prevBrakeTq;
        AbsActive = false;
        for (int i = 0; i < 4; i++)
        {
            float bt = brakePedal * (i < 2 ? brakeFront : brakeRear);
            if (AssistMode == Assist.Arcade && i >= 2) bt *= Mathf.Lerp(1f, 0.5f, Mathf.Clamp01(Mathf.Abs(steerSm) * 2.5f));         // braking while turning: keep the rear planted, the classic way to spin a car
            if (AssistMode != Assist.Off && bt > 0f)
            {   // ABS: back off when the wheel is decelerating faster than the tyre can follow
                float k = w[i].kappa; float scale = 1f - Mathf.Clamp01((-k - 0.16f) / 0.12f);
                if (scale < 0.999f) AbsActive = true; bt *= scale;
            }
            if (Handbrake && i >= 2) bt = Mathf.Max(bt, handbrakeTq);
            brakeTq[i] = bt;
        }

        // ---- 4. wheels: sub-stepped, semi-implicit angular velocity; tyre forces from the contact-patch velocity
        const int Sub = 4; float dtS = dt / Sub;
        float maxS = 0f;
        for (int i = 0; i < 4; i++)
        {
            var q = w[i];
            if (!q.grounded)
            {   // airborne: wheel just spins down under its own brake / drive torque
                float Tq = q.driven ? (i == dA ? tqL : tqR) : 0f;
                q.omega += dt * Tq / WheelI; float db = brakeTq[i] * dt / WheelI; q.omega = Mathf.Abs(q.omega) <= db ? 0f : q.omega - Mathf.Sign(q.omega) * db;
                q.spin += q.omega * dt; q.fy = 0; q.kappa = 0; q.s = 0; WheelFx[i] = Mathf.Lerp(WheelFx[i], 0f, 0.5f); WheelSlip[i] = 0; WheelKappa[i] = 0;
                continue;
            }
            var sp = Tyre.Props(q.surf);
            if (AssistMode == Assist.Arcade) sp.slide = Mathf.Lerp(sp.slide, 1f, 0.55f);                 // breakaway is gradual: grip falls away slowly past the limit instead of dropping off a cliff
            float loadFactor = Mathf.Clamp(1f - LoadSens * (q.fz / fzNom - 1f), 0.65f, 1.25f);
            float muX = Mu0 * muScale * (i < 2 ? muFrontScale : muRearScale) * sp.mu * loadFactor, muY = muX * MuYRatio;
            float vden = Mathf.Max(Mathf.Abs(q.vx), 1.0f);
            float tanA = q.vy / vden;
            float Td = q.driven ? (i == dA ? tqL : tqR) : 0f;
            float Ieff = WheelI;
            float fxSum = 0f;
            for (int sub = 0; sub < Sub; sub++)
            {
                float kappa = (q.omega * WheelR - q.vx) / vden;
                Tyre.Force(kappa, tanA, muX, muY, q.fz, sp.slide, out float fx1, out _, out _);
                Tyre.Force(kappa + 0.02f, tanA, muX, muY, q.fz, sp.slide, out float fx2, out _, out _);
                float K = Mathf.Max(0f, (fx2 - fx1) / 0.02f) * (WheelR / vden);           // dFx / d(omega)
                q.omega += (Td - WheelR * fx1) / (Ieff / dtS + WheelR * K);
                float db = brakeTq[i] * dtS / Ieff;
                q.omega = Mathf.Abs(q.omega) <= db ? 0f : q.omega - Mathf.Sign(q.omega) * db;
                fxSum += fx1;
            }
            q.spin += q.omega * dt;
            q.kappa = (q.omega * WheelR - q.vx) / vden;
            Tyre.Force(q.kappa, tanA, muX, muY, q.fz, sp.slide, out float fxEnd, out float fyTarget, out float sEnd);
            q.s = sEnd;
            float fxAvg = fxSum / Sub; q.fxAvg = fxAvg;
            // lateral force builds up over a relaxation length (removes low-speed chatter and gives a natural steering response)
            float kf = 1f - Mathf.Exp(-Mathf.Max(Mathf.Abs(q.vx), 2.0f) * dt / RelaxLen);
            q.fy += (fyTarget - q.fy) * kf;
            // rolling resistance
            float rr = -sp.rolling * q.fz * (float)System.Math.Tanh(q.vx / 0.6f);

            Vector3 apply = q.contact + q.n * 0.10f;                                  // roll-centre / anti-squat height
            Body.AddForceAtPosition(q.f * (fxAvg + rr) + q.r * q.fy, apply);

            WheelKappa[i] = q.kappa; WheelSlip[i] = q.s; WheelPoint[i] = q.contact; SurfaceUnderWheel[i] = q.surf;
            maxS = Mathf.Max(maxS, q.s);
            float longFx = Mathf.Clamp01((Mathf.Abs(q.kappa) - 0.24f) / 0.35f);
            float latFx = Mathf.Clamp01((q.s - 1.25f) * 1.4f) * Mathf.Clamp01((Mathf.Abs(q.vx) + Mathf.Abs(q.vy)) / 4f);
            WheelFx[i] = Mathf.Lerp(WheelFx[i], Mathf.Max(longFx, latFx), 0.35f);
        }
        SlipAmount = Mathf.Lerp(SlipAmount, grounded > 0 ? Mathf.Clamp01((maxS - 0.9f) / 1.2f) : 0f, 0.25f);

        // ---- 5. aero, belly contact, stability aid
        if (speed > 0.1f)
        {
            Body.AddForce(-vel.normalized * (0.5f * Rho * cdA * speed * speed));
            float down = 0.5f * Rho * clA * speed * speed;
            Body.AddForceAtPosition(-up * down * clFront, transform.TransformPoint(new Vector3(0, 0, FrontZ)));
            Body.AddForceAtPosition(-up * down * (1f - clFront), transform.TransformPoint(new Vector3(0, 0, RearZ)));
        }
        BellyContact(dt);
        if (AssistMode == Assist.Full && grounded >= 3) YawStability();
        if (AssistMode == Assist.Arcade && grounded >= 3) ArcadeAids();
    }

    // ------------------------------------------------------------------ pedals, steering
    void ReadPedals(float dt, float fwdSpeed, out float drive, out float brake)
    {
        float thr = Mathf.Clamp01(Throttle), brk = Mathf.Clamp01(Brake);
        bool reverse = drivetrain.Gear < 0;
        if (!reverse) { if (brk > 0.05f && thr < 0.05f && fwdSpeed < 0.8f) { reverse = true; drivetrain.SetReverse(true); } }
        else if (thr > 0.05f && brk < 0.05f && fwdSpeed > -0.8f) { reverse = false; drivetrain.SetReverse(false); }
        float d, b;
        if (reverse) { d = brk; b = (thr > 0.05f && fwdSpeed < -0.5f) ? thr : 0f; } else { d = thr; b = brk; }
        driveSm = Mathf.MoveTowards(driveSm, d, 7f * dt);          // foot travel
        brakeSm = Mathf.MoveTowards(brakeSm, b, 14f * dt);
        drive = driveSm; brake = brakeSm;
    }

    void UpdateSteering(float dt, float speed)
    {
        float rate = (Mathf.Abs(Steer) > Mathf.Abs(steerSm) ? 7f : 10f) / (1f + speed / 30f);                 // quick at parking speed, calmer as speed rises
        steerSm = Mathf.MoveTowards(steerSm, Mathf.Clamp(Steer, -1f, 1f), rate * dt);
        float dCmd = steerSm * MaxSteerRad(speed);
        if (AssistMode != Assist.Off && speed > 3f)
        {   // never ask the front tyres for more than ~1.4-1.8x peak slip: full lock at speed cannot plough, and a tail slide is caught by counter-steer
            Vector3 lv = transform.InverseTransformDirection(Body.linearVelocity), la = transform.InverseTransformDirection(Body.angularVelocity);
            if (lv.z > 1f)
            {
                float gammaF = Mathf.Atan2(lv.x + FrontZ * la.y, lv.z);
                float lim = AssistMode == Assist.Arcade ? 0.17f : AssistMode == Assist.Full ? 0.21f : 0.29f;
                lim *= Mathf.Lerp(3.5f, 1f, Mathf.InverseLerp(4f, 20f, speed));                                   // slow: the tyres cannot plough, so the wheel is nearly unlimited; fast: full protection
                float limited = Mathf.Clamp(dCmd, gammaF - lim, gammaF + lim);
                float wgt = Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(3f, 9f, speed));
                dCmd = Mathf.Lerp(dCmd, limited, wgt);
            }
        }
        if (AssistMode == Assist.Arcade && speed > 4f)
        {   // automatic counter-steer: when the car slides, the front wheels turn toward the direction of travel
            Vector3 lv2 = transform.InverseTransformDirection(Body.linearVelocity);
            if (lv2.z > 1.5f) dCmd += Mathf.Clamp(Mathf.Atan2(lv2.x, lv2.z) * 0.55f, -0.16f, 0.16f) * Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(4f, 12f, speed));
        }
        steerCentre = dCmd;
        // Ackermann geometry
        float ad = Mathf.Abs(dCmd), inner = 0f, outer = 0f;
        if (ad > 1e-4f) { float R = WheelBase / Mathf.Tan(ad); inner = Mathf.Atan(WheelBase / (R - TrackWidth * 0.5f)); outer = Mathf.Atan(WheelBase / (R + TrackWidth * 0.5f)); }
        bool right = dCmd > 0f;
        w[0].steer = right ? outer : -inner; w[1].steer = right ? inner : -outer;
    }

    // ------------------------------------------------------------------ geometry
    void Geometry(Wheel q, Vector3 up, float dt)
    {
        Vector3 mountW = transform.TransformPoint(q.mount);
        Vector3 fh = transform.forward; fh.y = 0f; fh = fh.sqrMagnitude < 1e-4f ? Vector3.forward : fh.normalized;
        Vector3 c0 = mountW - up * DStatic;
        // roll a wheel of real radius over the terrain: the centre must clear the terrain profile under the whole tyre
        float hEnv = -1e9f, gCentre = 0f; Surface surf = Surface.Grass;
        for (int k = -1; k <= 1; k++)
        {
            float del = k * 0.75f * WheelR;
            float g = World.GroundHeight(c0.x + fh.x * del, c0.z + fh.z * del, mountW.y, out Surface sf);
            if (k == 0) { gCentre = g; surf = sf; }
            hEnv = Mathf.Max(hEnv, g + Mathf.Sqrt(WheelR * WheelR - del * del));
        }
        float uy = Mathf.Max(up.y, 0.35f);
        float d = (mountW.y - hEnv) / uy;
        int wi = System.Array.IndexOf(w, q); if (wi >= 0) DebugEnvelope[wi] = hEnv;
        q.surf = surf;
        if (d >= DMax) { q.grounded = false; q.d = DMax; q.x = 0f; return; }
        q.grounded = true; q.d = Mathf.Max(d, DMin); q.x = DMax - q.d;
        Vector3 wc = mountW - up * q.d;
        const float e = 0.45f;
        float gxp = World.GroundHeight(wc.x + e, wc.z, mountW.y, out _), gxm = World.GroundHeight(wc.x - e, wc.z, mountW.y, out _);
        float gzp = World.GroundHeight(wc.x, wc.z + e, mountW.y, out _), gzm = World.GroundHeight(wc.x, wc.z - e, mountW.y, out _);
        q.n = new Vector3(-(gxp - gxm) / (2 * e), 1f, -(gzp - gzm) / (2 * e)).normalized;
        q.contact = wc - q.n * WheelR;
        Vector3 heading = Quaternion.AngleAxis(q.steer * Mathf.Rad2Deg, up) * transform.forward;
        q.f = Vector3.ProjectOnPlane(heading, q.n).normalized; q.r = Vector3.Cross(q.n, q.f);
        Vector3 v = Body.GetPointVelocity(q.contact);
        q.vx = Vector3.Dot(v, q.f); q.vy = Vector3.Dot(v, q.r);
        q.lastGround = gCentre;
    }

    /// <summary>
    /// Whole-hull ground contact. Any hull point that ends up below the ground is pushed out along the surface normal
    /// (stiff spring, one-sided damper) and dragged by Coulomb friction, so bumpers, sills and the roof interact with slopes as well as the wheels do.
    /// </summary>
    public void HullContact(float dt) => BellyContact(dt);
    void BellyContact(float dt)
    {
        foreach (var lp in belly)
        {
            Vector3 p = transform.TransformPoint(lp);
            float g = World.GroundHeight(p.x, p.z, p.y + 0.6f, out _);
            float depth = g - p.y;
            if (depth <= 0f) continue;
            float e = 0.35f;
            Vector3 n = new Vector3(-(World.GroundHeight(p.x + e, p.z, p.y, out _) - World.GroundHeight(p.x - e, p.z, p.y, out _)) / (2 * e), 1f, -(World.GroundHeight(p.x, p.z + e, p.y, out _) - World.GroundHeight(p.x, p.z - e, p.y, out _)) / (2 * e)).normalized;
            Vector3 v = Body.GetPointVelocity(p);
            float vn = Vector3.Dot(v, n);
            float F = Mathf.Clamp(420000f * depth - (vn < 0f ? 16000f * vn : 0f), 0f, 260000f);     // damp only while penetrating deeper: no suction on the way out
            Body.AddForceAtPosition(n * F, p);
            Vector3 vt = v - n * vn;
            if (vt.sqrMagnitude > 0.0004f) Body.AddForceAtPosition(-vt.normalized * Mathf.Min(0.6f * F, vt.magnitude * Mass * 0.25f / Mathf.Max(dt, 1e-3f)), p);
        }
    }

    /// <summary>Full assist: gentle yaw damping toward the yaw rate the steering asks for, off during handbrake turns.</summary>
    /// <summary>
    /// Arcade stability aid: (1) damps yaw that the steering did not ask for (spin), (2) gives a little extra turn-in when the car rotates less than asked,
    /// (3) rotates the car back toward its direction of travel once the sideslip passes ~9 degrees. Torque is capped at 6 rad/s2, so it feels like grip, not a hand.
    /// </summary>
    void ArcadeAids()
    {
        if (Handbrake) return;
        Vector3 lv = transform.InverseTransformDirection(Body.linearVelocity), la = transform.InverseTransformDirection(Body.angularVelocity);
        float v = Mathf.Sqrt(lv.x * lv.x + lv.z * lv.z); if (v < 6f || lv.z < 2f) return;
        float Iy = Body.inertiaTensor.y;
        float rDes = v * Mathf.Tan(steerCentre) / WheelBase / (1f + 0.0015f * v * v), err = la.y - rDes;
        float torque = 0f;
        if (Mathf.Abs(err) > 0.10f) torque -= Mathf.Sign(err) * (Mathf.Abs(err) - 0.10f) * (err * rDes < 0f || Mathf.Abs(la.y) > Mathf.Abs(rDes) ? 7f : 3f) * Iy;
        float beta = Mathf.Atan2(lv.x, lv.z);
        if (Mathf.Abs(beta) > 0.15f) torque += Mathf.Sign(beta) * (Mathf.Abs(beta) - 0.15f) * 9f * Iy;
        torque = Mathf.Clamp(torque, -6f * Iy, 6f * Iy);
        Body.AddTorque(transform.up * torque);
    }

    void YawStability()
    {
        if (Handbrake) return;
        float v = Body.linearVelocity.magnitude; if (v < 8f) return;
        Vector3 la = transform.InverseTransformDirection(Body.angularVelocity);
        float rDes = v * Mathf.Tan(steerCentre) / WheelBase / (1f + 0.0015f * v * v);
        float err = la.y - rDes;
        if (Mathf.Abs(err) > 0.35f) Body.AddTorque(transform.up * (-Mathf.Sign(err) * (Mathf.Abs(err) - 0.35f) * 900f));
    }

    void Update()
    {
        if (w == null) return;
        for (int i = 0; i < 4; i++)
        {
            var q = w[i];
            q.visual.localPosition = new Vector3(q.mount.x, -q.d, q.mount.z);
            q.visual.localRotation = Quaternion.Euler(0, q.steer * Mathf.Rad2Deg, 0) * Quaternion.Euler(q.spin * Mathf.Rad2Deg, 0, 0);
        }
    }

    void OnCollisionEnter(Collision c)
    {
        float v = c.relativeVelocity.magnitude;
        if (v > 2f) Impact?.Invoke(v);
        if (v > 2f) Log.I("car", $"collision with '{c.collider.name}' ({c.collider.transform.parent?.name}) rel speed {v:F1} m/s at {c.GetContact(0).point} cell {Grid.CellLabel(c.GetContact(0).point.x, c.GetContact(0).point.z)}");
    }

    public void Respawn(Vector3 pos, float heading = float.NaN)
    {
        float yaw = float.IsNaN(heading) ? transform.eulerAngles.y : heading;
        var rot = Quaternion.Euler(0, yaw, 0);
        Body.position = pos; Body.rotation = rot;
        transform.SetPositionAndRotation(pos, rot);
        Physics.SyncTransforms();
        Body.linearVelocity = Vector3.zero; Body.angularVelocity = Vector3.zero;
        flipTimer = 0f; ResetDynamics();
        Log.I("car", $"respawn at {pos} heading {yaw:F0}");
        Respawned?.Invoke();
    }
}
