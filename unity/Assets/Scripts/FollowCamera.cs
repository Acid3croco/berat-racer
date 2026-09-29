using UnityEngine;

public enum CamMode { Chase, Close, Hood, Bumper, Far }

/// <summary>
/// Camera rig with five views (C / D-pad up to cycle), free look (right stick, or hold right mouse) that recentres itself,
/// and a rear view (hold B / R3). Speed effects: FOV widening, look-ahead, acceleration pull-back, shake and impact kicks.
/// </summary>
public class FollowCamera : MonoBehaviour
{
    public CarController Car;
    public CamMode Mode = CamMode.Chase;
    public float RestFov = 72f, MaxFov = 96f, FullSpeedKmh = 200f;   // vertical FOV 70 -> 100 deg (research: >100 stretches the edges badly)

    struct Rig { public string name; public float dist, height, look, fov, shake; public bool rigid; public Vector3 local; public float pitch; }
    Rig[] Rigs = new Rig[5];
    public void Configure(CarSpec sp)
    {
        float L = sp.Length, H = sp.Height, gy = sp.GroundY;
        Rigs[0] = new Rig { name = "Chase", dist = L * 1.12f, height = H * 0.42f, look = 5f, fov = 0, shake = 1f };
        Rigs[1] = new Rig { name = "Close chase", dist = L * 0.86f, height = H * 0.24f, look = 6f, fov = 3, shake = 1f };
        Rigs[2] = new Rig { name = "Hood", rigid = true, local = new Vector3(0, gy + H * 0.86f, sp.A - 0.95f), pitch = -2f, fov = 6, shake = 0.6f };
        Rigs[3] = new Rig { name = "Bumper", rigid = true, local = new Vector3(0, gy + 0.42f, sp.ZFront - 0.02f), pitch = 0f, fov = 8, shake = 0.5f };
        Rigs[4] = new Rig { name = "Far chase", dist = L * 2.5f, height = H * 3.2f, look = 6f, fov = -5, shake = 0.5f };
    }
    /// <summary>Angle (deg) between where the camera looks and the car's centre. Used by the -camtest mode.</summary>
    public float AimErrorDeg => Car == null ? 0f : Vector3.Angle(transform.forward, (Car.transform.position + Vector3.up * 1.1f) - transform.position);
    public string ModeName => Rigs[(int)Mode].name;
    public float ModeShownAt { get; private set; } = -10f;

    // g-forces as felt by the driver, sampled every physics step: lateral (cornering), vertical (bumps, compressions, crests) and longitudinal (braking dive)
    Vector3 prevVel, gSm;                                                        // gSm: x lateral, y vertical, z longitudinal (m/s2, smoothed)
    public Vector3 GForce => gSm;
    void FixedUpdate()
    {
        if (Car == null) return;
        Vector3 v = Car.Body.linearVelocity; Vector3 a = (v - prevVel) / Mathf.Max(Time.fixedDeltaTime, 1e-4f); prevVel = v;
        Vector3 loc = new Vector3(Vector3.Dot(a, Car.transform.right), a.y, Vector3.Dot(a, Car.transform.forward));
        loc = new Vector3(Mathf.Clamp(loc.x, -40f, 40f), Mathf.Clamp(loc.y, -40f, 40f), Mathf.Clamp(loc.z, -40f, 40f));
        float k = 1f - Mathf.Exp(-9f * Time.fixedDeltaTime);
        gSm = Vector3.Lerp(gSm, loc, k);
    }

    Vector3 heading = Vector3.forward, offSm;
    float impact, accelSm, yawOff, pitchOff, lastLook;
    bool hooked, lookBack;
    Camera cam;

    void Start() { cam = GetComponent<Camera>(); }

    public void NextMode()
    {
        Mode = (CamMode)(((int)Mode + 1) % Rigs.Length);
        ModeShownAt = Time.unscaledTime; Snap();
        Log.I("camera", "mode -> " + ModeName);
    }
    public void SetCar(CarController c) { Car = c; hooked = false; if (c != null && c.Spec != null) Configure(c.Spec); Snap(); }
    public void Look(Vector2 d) { yawOff += d.x; pitchOff = Mathf.Clamp(pitchOff - d.y, -15f, 60f); lastLook = Time.unscaledTime; }
    public void SetFreeLook(float yaw, float pitch) { yawOff = yaw; pitchOff = pitch; lastLook = Time.unscaledTime; }
    public void LookBack(bool on) { lookBack = on; }

    void LateUpdate()
    {
        if (Car == null) return;
        if (!hooked) { Car.Impact += v => impact = Mathf.Max(impact, Mathf.Clamp01(v / 14f)); hooked = true; }
        float dt = Mathf.Max(Time.deltaTime, 1e-4f);
        var rig = Rigs[(int)Mode];
        float sp = Car.SpeedKmh, spN = Mathf.Clamp01(sp / FullSpeedKmh);

        // free look recentres a moment after the stick / mouse is released
        if (Time.unscaledTime - lastLook > 1.3f) { yawOff = Mathf.MoveTowards(yawOff, 0f, 140f * dt); pitchOff = Mathf.MoveTowards(pitchOff, 0f, 70f * dt); }
        float yaw = yawOff + (lookBack ? 180f : 0f);

        // heading the chase camera trails: the car's direction of travel once moving, smoothed
        var v = Car.Body.linearVelocity; v.y = 0;
        Vector3 want = Car.transform.forward; want.y = 0; want.Normalize();
        if (v.magnitude > 6f && Vector3.Dot(v.normalized, want) > 0.3f) want = Vector3.Slerp(want, v.normalized, 0.6f);
        heading = Vector3.Slerp(heading, want, 1f - Mathf.Exp(-3.2f * dt)).normalized;

        accelSm = Mathf.Lerp(accelSm, Car.LongAccel, 1f - Mathf.Exp(-2.5f * dt));     // per-physics-step acceleration, smoothed
        float pull = Mathf.Clamp(accelSm * 0.05f, -0.5f, 0.9f);                       // drops back when accelerating

        if (rig.rigid)
        {
            Vector3 p = Car.transform.TransformPoint(rig.local);
            Quaternion r = Car.transform.rotation * Quaternion.Euler(rig.pitch + pitchOff * -0.6f, yaw, 0f);
            transform.position = p;
            transform.rotation = Quaternion.Slerp(transform.rotation, r, 1f - Mathf.Exp(-12f * dt));      // cockpit views filter the suspension jitter
        }
        else
        {
            Vector3 dirH = Quaternion.Euler(0, yaw, 0) * heading;                      // direction the camera looks along (horizontal)
            Vector3 focus = Car.transform.position + Vector3.up * 1.1f;
            Vector3 back = -dirH * (rig.dist + pull * 0.4f);                                // the follow distance stays put at any speed: the FOV carries the speed
            Vector3 offs = back + Vector3.up * rig.height;
            offs = Quaternion.AngleAxis(pitchOff, Vector3.Cross(Vector3.up, dirH)) * offs;   // orbit up / down
            // smooth the offset from the car, never the world position: a world-space filter trails the car by speed / rate metres and the distance would grow with speed
            offSm = Vector3.Lerp(offSm, offs, 1f - Mathf.Exp(-14f * dt));
            transform.position = focus + offSm;
            if (Car.World != null && !Car.World.Flat)
            {   // stay off the ground when close to it
                float gy = Car.World.GroundHeight(transform.position.x, transform.position.z, transform.position.y, out _) + 0.45f;
                if (transform.position.y < gy) transform.position = new Vector3(transform.position.x, gy, transform.position.z);
            }
            // free look orbits AROUND the car: the further you swing away from the default view, the more the look-ahead fades and the aim settles on the car itself
            float free = Mathf.Clamp01(Mathf.Abs(Mathf.DeltaAngle(0f, yaw)) / 20f + Mathf.Abs(pitchOff) / 20f);
            Vector3 lookAt = focus + dirH * (rig.look + sp * 0.045f) * (1f - free);
            transform.rotation = Quaternion.LookRotation(lookAt - transform.position, Vector3.up);
        }

        // g-force feel: the camera moves like the driver's head. Cornering slides it to the outside and leans it, bumps and compressions push it down, crests float it up, braking dives it.
        float gScale = rig.rigid ? 0.3f : 1f, gk = Mathf.Clamp01(sp / 25f);                        // stationary: no sway from small jitters
        Vector3 gs = gSm;
        Vector3 head = transform.right * Mathf.Clamp(-gs.x * 0.004f, -0.10f, 0.10f) * gk + Vector3.up * Mathf.Clamp(-gs.y * 0.010f, -0.22f, 0.22f) * gk;
        transform.position += head * gScale;
        float pitchG = Mathf.Clamp(-gs.z * 0.10f - gs.y * 0.03f, -2.5f, 3.5f) * gk;
        transform.rotation = Quaternion.AngleAxis(pitchG * gScale, transform.right) * transform.rotation;

        // shake: gentle low-frequency rumble (speed^2, slip) + decaying impact kicks
        float amp = (0.0004f + 0.0022f * spN * spN + 0.0015f * Car.SlipAmount) * rig.shake + 0.07f * impact;
        impact = Mathf.Max(0f, impact - 2.5f * dt);
        float tt = Time.time * 16f;
        transform.position += transform.right * ((Mathf.PerlinNoise(tt, 0.3f) - 0.5f) * 2f * amp) + transform.up * ((Mathf.PerlinNoise(0.7f, tt) - 0.5f) * 2f * amp);
        transform.rotation *= Quaternion.Euler(0, 0, (Mathf.PerlinNoise(5.1f, tt * 0.5f) - 0.5f) * 2f * amp * 6f);

        if (cam != null)
        {
            float fov = Mathf.Lerp(RestFov, MaxFov, Mathf.Pow(spN, 0.8f)) + rig.fov;
            cam.fieldOfView = Mathf.Lerp(cam.fieldOfView, fov, 1f - Mathf.Exp(-4f * dt));
        }
    }

    public void Snap()
    {
        if (Car == null) return;
        Vector3 f = Car.transform.forward; f.y = 0; heading = f.normalized; yawOff = pitchOff = 0f; accelSm = 0f; gSm = Vector3.zero; prevVel = Car.Body.linearVelocity;
        var rig = Rigs[(int)Mode];
        offSm = -heading * rig.dist + Vector3.up * rig.height;
        transform.position = rig.rigid ? Car.transform.TransformPoint(rig.local) : Car.transform.position + Vector3.up * 1.1f + offSm;
        transform.rotation = rig.rigid ? Car.transform.rotation : Quaternion.LookRotation(Car.transform.position + Vector3.up * 1.1f - transform.position, Vector3.up);
    }
}
