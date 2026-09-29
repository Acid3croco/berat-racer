using UnityEngine;
using UnityEngine.InputSystem;

/// <summary>
/// Sim-arcade car: rigid body + 4 spring-damper wheels sampling the LiDAR terrain heightfield,
/// Pacejka-style lateral grip that falls off past the slip peak (so it drifts), friction circle,
/// RWD power curve, handbrake, speed-sensitive steering and a little yaw/downforce assistance.
/// </summary>
[RequireComponent(typeof(Rigidbody))]
public class CarController : MonoBehaviour
{
    public WorldBuilder World;

    // ---- tuning
    public float Mass = 1250f;
    public float Power = 135000f;             // W  (~245 km/h top speed)
    public float MaxDriveForce = 9500f;       // N (traction-limited launch)
    public float BrakeForce = 1.15f;          // x wheel load
    public float MaxSteerLow = 33f, MaxSteerHigh = 6.5f;   // degrees at 0 and 60 m/s
    public float SpringK = 26000f, Damper = 3300f, RestLength = 0.5f * CarVisual.S, WheelRadius = 0.33f * CarVisual.S;
    public float FrontGrip = 1.45f, RearGrip = 1.38f;
    public float Downforce = 0.9f;
    public float YawAssist = 380f;

    // ---- input (set by a driver: player or smoke test)
    [HideInInspector] public float Throttle, Brake, Steer;
    [HideInInspector] public bool Handbrake;

    public float SpeedKmh => Body.linearVelocity.magnitude * 3.6f;
    public float ForwardSpeed => Vector3.Dot(Body.linearVelocity, transform.forward);
    public Rigidbody Body { get; private set; }
    public int WheelsOnGround { get; private set; }
    /// <summary>0..1 how hard the tyres are sliding (drives squeal and camera shake).</summary>
    public float SlipAmount { get; private set; }
    /// <summary>Longitudinal acceleration (m/s^2), measured per physics step and smoothed. Used by the camera pull-back.</summary>
    public float LongAccel { get; private set; }
    float prevFwdSpeed;
    /// <summary>Per wheel: 0..1 smoke/skid intensity (wheelspin, lock-up, sideways slide), contact point and surface.</summary>
    public readonly float[] WheelFx = new float[4];
    public readonly Vector3[] WheelPoint = new Vector3[4];
    public readonly Surface[] SurfaceUnderWheel = new Surface[4];
    public event System.Action Respawned;
    public event System.Action<float> Impact;   // relative speed of a collision, m/s
    public Surface CurrentSurface { get; private set; }

    struct Wheel { public Vector3 mount; public bool front; public Transform visual; public float compression, spin; public bool grounded; }
    Wheel[] wheels;
    float steerAngle;

    public void Init(WorldBuilder world, Transform[] wheelVisuals)
    {
        World = world;
        Body = GetComponent<Rigidbody>();
        Body.mass = Mass; Body.centerOfMass = new Vector3(0, -0.28f, 0.05f) * CarVisual.S;
        Body.linearDamping = 0.02f; Body.angularDamping = 0.6f;
        Body.interpolation = RigidbodyInterpolation.Interpolate;
        Body.collisionDetectionMode = CollisionDetectionMode.ContinuousDynamic;
        var box = gameObject.AddComponent<BoxCollider>(); box.enabled = System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-nocarcol") < 0; box.center = new Vector3(0, -0.1f, 0) * CarVisual.S; box.size = new Vector3(1.8f, 0.7f, 4.2f) * CarVisual.S;
        var mat = new PhysicsMaterial { dynamicFriction = 0.1f, staticFriction = 0.1f, bounciness = 0.05f };
        box.material = mat;
        var hits = Physics.OverlapBox(transform.TransformPoint(box.center), box.size * 0.5f, transform.rotation);
        Log.I("car", $"init: {hits.Length} colliders overlap spawn box: " + string.Join(", ", System.Array.ConvertAll(hits, h => h.name + "@" + h.transform.parent?.name + " " + h.bounds.size)));
        wheels = new Wheel[4];
        float S = CarVisual.S;
        Vector3[] m = { new Vector3(-0.85f * S, 0, 1.35f * S), new Vector3(0.85f * S, 0, 1.35f * S), new Vector3(-0.85f * S, 0, -1.3f * S), new Vector3(0.85f * S, 0, -1.3f * S) };
        for (int i = 0; i < 4; i++) wheels[i] = new Wheel { mount = m[i], front = i < 2, visual = wheelVisuals[i], compression = 0.1f };
    }

    void FixedUpdate()
    {
        if (World == null || !World.Ready) return;
        float dt = Time.fixedDeltaTime;
        Vector3 vel = Body.linearVelocity;
        float speed = vel.magnitude;
        float fwdSpeed = ForwardSpeed;
        LongAccel = Mathf.Lerp(LongAccel, Mathf.Clamp((fwdSpeed - prevFwdSpeed) / dt, -25f, 25f), 0.03f); prevFwdSpeed = fwdSpeed;

        // speed-sensitive steering with smoothing
        float maxSteer = Mathf.Lerp(MaxSteerLow, MaxSteerHigh, Mathf.Clamp01(speed / 60f));
        float target = Steer * maxSteer;
        steerAngle = Mathf.MoveTowards(steerAngle, target, (Mathf.Abs(target) > Mathf.Abs(steerAngle) ? 110f : 220f) * dt);

        // resolve pedals: brake pedal reverses once stopped
        float drive = Throttle, brake = Brake;
        if (Brake > 0.05f && fwdSpeed < 0.8f && Throttle < 0.05f) { drive = -Brake * 0.55f; brake = 0; }

        dbgSteps++;
        float floor = World.Data.TerrainHeight(transform.position.x, transform.position.z);
        if (transform.position.y < floor - 15f || transform.position.y > floor + 400f || float.IsNaN(transform.position.y))
        {
            Log.I("car", $"out of world (y={transform.position.y:F1}, floor={floor:F1}) -> safety respawn");
            Respawn(new Vector3(float.IsNaN(transform.position.x) ? 0 : transform.position.x, floor + 1.2f, float.IsNaN(transform.position.z) ? 0 : transform.position.z));
            return;
        }
        if (dbgSteps <= 4 || (dbgSteps <= 60 && dbgSteps % 10 == 0))
            Log.I("phys", $"body step {dbgSteps} y={transform.position.y:F2} vel={Body.linearVelocity} angVel={Body.angularVelocity} sleeping={Body.IsSleeping()} grav={Physics.gravity} mass={Body.mass} com={Body.centerOfMass}");
        int grounded = 0; float slipMax = 0f;
        Vector3 up = transform.up;
        Vector3 comWorld = Body.worldCenterOfMass;
        for (int i = 0; i < 4; i++)
        {
            var w = wheels[i];
            Vector3 mountW = transform.TransformPoint(w.mount);
            float gy = World.GroundHeight(mountW.x, mountW.z, mountW.y, out Surface surf);
            if (i == 0) CurrentSurface = surf;
            Vector3 n = GroundNormal(mountW.x, mountW.z, mountW.y);
            float hit = (mountW.y - gy) * Mathf.Max(0.5f, Vector3.Dot(up, Vector3.up));
            float comp = RestLength + WheelRadius - hit;
            w.grounded = comp > 0f;
            float prev = w.compression; w.compression = Mathf.Clamp(comp, 0f, RestLength + 0.12f);
            if (!w.grounded) { WheelFx[i] = Mathf.Lerp(WheelFx[i], 0f, 0.5f); wheels[i] = w; continue; }
            grounded++;

            float compVel = (comp - prev) / dt;
            float fz = SpringK * comp + Damper * compVel;
            if (comp > RestLength) fz += SpringK * 8f * (comp - RestLength);     // bump stop
            fz = Mathf.Max(0f, fz);
            Vector3 contact = mountW - up * (RestLength - w.compression + WheelRadius);
            Body.AddForceAtPosition(n * fz, contact);
            if (dbgSteps < 400 && dbgSteps % 8 == 0)
                Log.I("phys", $"step {dbgSteps} w{i} mountY={mountW.y:F2} groundY={gy:F2} hit={hit:F2} comp={comp:F3} compVel={compVel:F2} fz={fz:F0} n={n} v={Body.linearVelocity.magnitude:F1} y={transform.position.y:F2}");

            // wheel frame on the ground plane
            Quaternion steerRot = w.front ? Quaternion.AngleAxis(steerAngle, up) : Quaternion.identity;
            Vector3 f = Vector3.ProjectOnPlane(steerRot * transform.forward, n).normalized;
            Vector3 r = Vector3.Cross(n, f);
            Vector3 v = Body.GetPointVelocity(contact);
            float vf = Vector3.Dot(v, f), vr = Vector3.Dot(v, r);

            float mu = surf == Surface.Asphalt ? 1f : surf == Surface.Dirt ? 0.72f : 0.6f;
            float grip = (w.front ? FrontGrip : RearGrip) * mu;
            if (Handbrake && !w.front) grip *= 0.42f;
            float maxF = grip * fz;

            // longitudinal
            float fx = 0f;
            if (!w.front) fx += drive >= 0 ? Mathf.Min(drive * MaxDriveForce, drive * Power / Mathf.Max(Mathf.Abs(vf), 6f)) * 0.5f : drive * MaxDriveForce * 0.35f;
            if (brake > 0f) fx -= Mathf.Sign(vf) * brake * BrakeForce * fz * (w.front ? 0.62f : 0.38f) * mu * Mathf.Min(1f, Mathf.Abs(vf) * 0.8f);
            if (Handbrake && !w.front) fx -= Mathf.Sign(vf) * fz * 0.7f * Mathf.Min(1f, Mathf.Abs(vf));
            fx -= Mathf.Sign(vf) * fz * 0.012f;                                  // rolling resistance
            float demand = Mathf.Abs(fx) / Mathf.Max(maxF, 1f);                       // >1 means the tyre cannot transmit the force asked of it
            fx = Mathf.Clamp(fx, -maxF, maxF);

            // lateral: slip-angle curve with post-peak falloff
            float slip = Mathf.Atan2(vr, Mathf.Abs(vf) + 1.0f);
            slipMax = Mathf.Max(slipMax, Mathf.Abs(slip) / 0.30f);
            float fyMag = maxF * Mathf.Sin(1.45f * Mathf.Atan(7.5f * Mathf.Abs(slip)));
            float circle = Mathf.Sqrt(Mathf.Max(0f, 1f - (fx * fx) / Mathf.Max(maxF * maxF, 1f)));
            float fy = -Mathf.Sign(slip) * fyMag * Mathf.Lerp(1f, circle, 0.9f);
            float stopForce = Mass * 0.25f * Mathf.Abs(vr) / dt;                 // never overshoot the zero-slip velocity
            fy = Mathf.Clamp(fy, -stopForce, stopForce);

            // apply at COM height for stability (avoids tire forces rolling the body)
            Vector3 at = new Vector3(contact.x, comWorld.y - 0.1f, contact.z);
            Body.AddForceAtPosition(f * fx + r * fy, at);

            // smoke / skid intensity: wheelspin or lock-up (demand above grip) and sideways slide (slip angle past the peak)
            float longFx = Mathf.Clamp01((demand - 0.85f) * 3.5f) * (fz > 400f ? 1f : 0f);
            float latFx = Mathf.Clamp01(Mathf.Abs(slip) / 0.30f - 0.55f) * 2.2f * Mathf.Clamp01((Mathf.Abs(vf) + Mathf.Abs(vr)) / 4f);
            WheelFx[i] = Mathf.Lerp(WheelFx[i], Mathf.Clamp01(Mathf.Max(longFx, latFx)), 0.35f);
            WheelPoint[i] = contact; SurfaceUnderWheel[i] = surf;
            w.spin += vf / WheelRadius * dt;
            wheels[i] = w;
        }
        WheelsOnGround = grounded;
        SlipAmount = Mathf.Lerp(SlipAmount, Mathf.Clamp01(slipMax) * (grounded > 0 ? 1f : 0f), 0.25f);

        // aero
        if (speed > 0.1f)
        {
            Body.AddForce(-vel.normalized * 0.42f * speed * speed);
            if (grounded > 0) Body.AddForce(-up * Downforce * speed * speed);
        }
        // arcade assist: damp yaw a little so drifts are catchable
        if (grounded >= 3) Body.AddTorque(-up * (Vector3.Dot(Body.angularVelocity, up)) * YawAssist * (Handbrake ? 0.25f : 1f));

        // flipped? put it back after a moment
        if (Vector3.Dot(up, Vector3.up) < 0.25f && speed < 3f) { flipTimer += dt; if (flipTimer > 2.5f) Respawn(transform.position + Vector3.up * 1f); }
        else flipTimer = 0f;
    }
    float flipTimer; int dbgSteps;

    void OnCollisionEnter(Collision c)
    {
        Log.I("car", $"COLLISION-ENTER with '{c.collider.name}' parent={c.collider.transform.parent?.name} contacts={c.contactCount} impulse={c.impulse.magnitude:F0} pos={c.GetContact(0).point} sep={c.GetContact(0).separation:F2}");
        float v = c.relativeVelocity.magnitude;
        if (v > 2f) Impact?.Invoke(v);
        if (v > 2f) Log.I("car", $"collision with '{c.collider.name}' ({c.collider.transform.parent?.name}) rel speed {v:F1} m/s at {c.GetContact(0).point}");
    }

    Vector3 GroundNormal(float x, float z, float refY)
    {
        const float e = 1.2f;
        float hx = World.GroundHeight(x + e, z, refY, out _) - World.GroundHeight(x - e, z, refY, out _);
        float hz = World.GroundHeight(x, z + e, refY, out _) - World.GroundHeight(x, z - e, refY, out _);
        return new Vector3(-hx, 2 * e, -hz).normalized;
    }

    void Update()
    {
        if (wheels == null) return;
        for (int i = 0; i < 4; i++)
        {
            var w = wheels[i];
            float y = w.mount.y - (RestLength - w.compression);
            w.visual.localPosition = new Vector3(w.mount.x, y, w.mount.z);
            float steer = w.front ? steerAngle : 0f;
            w.visual.localRotation = Quaternion.Euler(w.spin * Mathf.Rad2Deg, steer, 0f);
            // keep the spin axis in the wheel frame: yaw first, then spin around X
            w.visual.localRotation = Quaternion.Euler(0, steer, 0) * Quaternion.Euler(w.spin * Mathf.Rad2Deg, 0, 0);
        }
    }

    public void Respawn(Vector3 pos, float heading = float.NaN)
    {
        float yaw = float.IsNaN(heading) ? transform.eulerAngles.y : heading;
        var rot = Quaternion.Euler(0, yaw, 0);
        Body.position = pos; Body.rotation = rot;                               // with interpolation the Rigidbody pose wins over the transform
        transform.SetPositionAndRotation(pos, rot);
        Physics.SyncTransforms();
        Body.linearVelocity = Vector3.zero; Body.angularVelocity = Vector3.zero;
        flipTimer = 0f; steerAngle = 0f;
        Log.I("car", $"respawn at {pos} heading {yaw:F0}");
        Respawned?.Invoke();
    }
}
