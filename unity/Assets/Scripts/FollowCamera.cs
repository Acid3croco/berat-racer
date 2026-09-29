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
    public float RestFov = 70f, MaxFov = 100f, FullSpeedKmh = 250f;   // vertical FOV 70 -> 100 deg (research: >100 stretches the edges badly)

    struct Rig { public string name; public float dist, height, look, fov, shake; public bool rigid; public Vector3 local; public float pitch; }
    static readonly Rig[] Rigs = {
        new Rig { name = "Chase",       dist = 5.3f, height = 1.9f, look = 4f,  fov = 0,  shake = 1f },
        new Rig { name = "Close chase", dist = 3.7f, height = 1.35f, look = 5f, fov = 3,  shake = 1f },
        new Rig { name = "Hood",        rigid = true, local = new Vector3(0, 0.50f, 0.72f) * CarVisual.S, pitch = -2f, fov = 6, shake = 0.6f },
        new Rig { name = "Bumper",      rigid = true, local = new Vector3(0, -0.12f, 2.05f) * CarVisual.S, pitch = 0f, fov = 8, shake = 0.5f },
        new Rig { name = "Far chase",   dist = 10.5f, height = 4.4f, look = 6f, fov = -5, shake = 0.5f },
    };
    public string ModeName => Rigs[(int)Mode].name;
    public float ModeShownAt { get; private set; } = -10f;

    Vector3 heading = Vector3.forward;
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
    public void Look(Vector2 d) { yawOff += d.x; pitchOff = Mathf.Clamp(pitchOff - d.y, -15f, 60f); lastLook = Time.unscaledTime; }
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
            transform.rotation = Quaternion.Slerp(transform.rotation, r, 1f - Mathf.Exp(-28f * dt));
        }
        else
        {
            Vector3 dirH = Quaternion.Euler(0, yaw, 0) * heading;                      // direction the camera looks along (horizontal)
            Vector3 focus = Car.transform.position + Vector3.up * 1.1f;
            Vector3 back = -dirH * (rig.dist + sp * 0.005f + pull);
            Vector3 offs = back + Vector3.up * (rig.height + sp * 0.003f);
            offs = Quaternion.AngleAxis(pitchOff, Vector3.Cross(Vector3.up, dirH)) * offs;   // orbit up / down
            Vector3 pos = focus + offs;
            transform.position = Vector3.Lerp(transform.position, pos, 1f - Mathf.Exp(-9f * dt));
            Vector3 lookAt = focus + dirH * (rig.look + sp * 0.045f);
            transform.rotation = Quaternion.LookRotation(lookAt - transform.position, Vector3.up);
        }

        // shake: gentle low-frequency rumble (speed^2, slip) + decaying impact kicks
        float amp = (0.002f + 0.010f * spN * spN + 0.008f * Car.SlipAmount) * rig.shake + 0.30f * impact;
        impact = Mathf.Max(0f, impact - 2.5f * dt);
        float tt = Time.time * 16f;
        transform.position += transform.right * ((Mathf.PerlinNoise(tt, 0.3f) - 0.5f) * 2f * amp) + transform.up * ((Mathf.PerlinNoise(0.7f, tt) - 0.5f) * 2f * amp);
        transform.rotation *= Quaternion.Euler(0, 0, (Mathf.PerlinNoise(5.1f, tt * 0.5f) - 0.5f) * 2f * amp * 18f);

        if (cam != null)
        {
            float fov = Mathf.Lerp(RestFov, MaxFov, spN) + rig.fov;
            cam.fieldOfView = Mathf.Lerp(cam.fieldOfView, fov, 1f - Mathf.Exp(-4f * dt));
        }
    }

    public void Snap()
    {
        if (Car == null) return;
        Vector3 f = Car.transform.forward; f.y = 0; heading = f.normalized; yawOff = pitchOff = 0f; accelSm = 0f;
        var rig = Rigs[(int)Mode];
        transform.position = rig.rigid ? Car.transform.TransformPoint(rig.local) : Car.transform.position + Vector3.up * 1.1f - heading * rig.dist + Vector3.up * rig.height;
        transform.rotation = rig.rigid ? Car.transform.rotation : Quaternion.LookRotation(Car.transform.position + Vector3.up * 1.1f - transform.position, Vector3.up);
    }
}
