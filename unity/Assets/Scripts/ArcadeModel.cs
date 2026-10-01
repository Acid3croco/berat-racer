using UnityEngine;

/// <summary>
/// Our own arcade car, written from scratch on the shared chassis: four corner springs to the ground, per-wheel grip with a friction circle, but grip that is tuned for fun,
/// not for measurement: strong self-aligning yaw control (the car turns where you point it), sideways speed drains away quickly, drifts are started with the handbrake or by
/// pitching power into a corner, and top speed comes from power and drag. No tyre curve, no clutch, no slip-angle limited steering.
/// </summary>
public class ArcadeModel : DrivingModel
{
    public override string Name => "Arcade";
    public override string Info => "own simple model: strong yaw control, fast grip recovery, handbrake drifts";
    float steerSm, rpm; int gear = 1; float load;
    float ratio;
    public override float Rpm => rpm;
    public override int Gear => gear;
    public override float Load => load;

    const float Rho = 1.2f;
    float[] springK = new float[4], damperC = new float[4], staticLoad = new float[4];
    readonly float[] spin = new float[4];

    public override void Attach(CarController host)
    {
        base.Attach(host);
        var s = car.Spec; float g = 9.81f;
        // static corner loads from the weight distribution; spring rate so that the body sags 12 cm on its springs (about 1.4 Hz)
        for (int i = 0; i < 4; i++)
        {
            float share = (i < 2 ? s.FrontWeight : 1f - s.FrontWeight) * 0.5f;
            staticLoad[i] = s.Mass * g * share; springK[i] = staticLoad[i] / 0.12f;
            damperC[i] = 2f * 0.45f * Mathf.Sqrt(springK[i] * s.Mass * share);
        }
    }

    public override void OnReset() { steerSm = 0; rpm = car.Spec.Idle; gear = 1; }

    public override void FixedStep(float dt)
    {
        var s = car.Spec; var body = car.Body; var tr = car.transform; var world = car.World;
        Vector3 up = tr.up, vel = body.linearVelocity; float speed = vel.magnitude;
        Vector3 lv = tr.InverseTransformDirection(vel);
        // ---- inputs: brake at a standstill selects reverse, like the real thing
        bool reverse = lv.z < 0.8f && car.Brake > 0.05f && car.Throttle < 0.05f;
        float thr = reverse ? car.Brake : car.Throttle, brk = reverse ? 0f : car.Brake;
        if (lv.z < -0.5f && car.Throttle > 0.05f) { brk = car.Throttle; thr = 0f; }
        steerSm = car.SteerDirect ? Mathf.Clamp(car.Steer, -1f, 1f) : Mathf.MoveTowards(steerSm, Mathf.Clamp(car.Steer, -1f, 1f), (Mathf.Abs(car.Steer) > Mathf.Abs(steerSm) ? 6f : 10f) / (1f + speed / 40f) * dt);
        float steerAngle = steerSm * car.MaxSteerRad(speed);

        // ---- a plausible gearbox for the sound and the HUD: pick the gear that keeps the engine in its power band
        float wheelRpm = Mathf.Abs(lv.z) / s.WheelR * 60f / (2f * Mathf.PI);
        gear = 1;
        for (int gi = 0; gi < s.Ratios.Length; gi++) { gear = gi + 1; if (wheelRpm * s.Ratios[gi] * s.FinalDrive < s.Redline * 0.88f) break; }
        if (lv.z < -0.5f || reverse) gear = -1;
        ratio = gear > 0 ? s.Ratios[gear - 1] : Mathf.Abs(s.ReverseRatio);
        float target = Mathf.Max(s.Idle, wheelRpm * ratio * s.FinalDrive);
        rpm = Mathf.Lerp(rpm, Mathf.Min(target, s.Redline), 0.35f);
        load = Mathf.Lerp(load, thr, 0.3f);
        float engineForce = gear == 0 ? 0f : s.TorqueAt(rpm) * s.Efficiency * ratio * s.FinalDrive / s.WheelR * (rpm >= s.Redline * 0.99f ? 0f : 1f);   // N at the tyres, all driven wheels
        // ---- suspension + grip at every wheel
        int grounded = 0; Surface surf0 = Surface.Grass; float slipSum = 0f;
        Vector3 upN = Vector3.up;
        for (int i = 0; i < 4; i++)
        {
            Vector3 mount = tr.TransformPoint(s.Mount(i));
            float ground = world.GroundHeight(mount.x, mount.z, mount.y, out Surface surf);
            float len = mount.y - ground, freeLen = s.WheelRadiusSum + 0.12f, x = freeLen - len;
            car.WheelPoint[i] = new Vector3(mount.x, ground, mount.z); car.SurfaceUnderWheel[i] = surf; if (i == 0) surf0 = surf;
            car.DebugEnvelope[i] = ground;
            bool on = x > 0f; float fz = 0f;
            if (on)
            {
                grounded++;
                Vector3 pv = body.GetPointVelocity(mount);
                fz = Mathf.Clamp(springK[i] * x - damperC[i] * pv.y, 0f, staticLoad[i] * 4f);
                body.AddForceAtPosition(upN * fz, mount);
            }
            car.WheelLoad[i] = fz;
            // wheel visual
            float d = Mathf.Clamp(len - s.WheelR, 0.14f, 0.52f);
            var vis = car.WheelVisuals[i];
            bool front = i < 2;
            float wheelSteer = front ? steerAngle : 0f;
            vis.localPosition = new Vector3(s.Mount(i).x, -d, s.Mount(i).z);
            vis.localRotation = Quaternion.Euler(0, wheelSteer * Mathf.Rad2Deg, 0) * Quaternion.Euler(spin[i] * Mathf.Rad2Deg, 0, 0);
            if (!on) { car.WheelFx[i] = Mathf.Lerp(car.WheelFx[i], 0f, 0.4f); continue; }

            // grip: friction circle, mu tuned for fun
            Vector3 fwdW = Quaternion.AngleAxis(wheelSteer * Mathf.Rad2Deg, up) * tr.forward, rightW = Vector3.Cross(up, fwdW);
            Vector3 wv = body.GetPointVelocity(mount); float vf = Vector3.Dot(wv, fwdW), vs = Vector3.Dot(wv, rightW);
            var props = Tyre.Props(surf);
            float mu = props.mu * s.MuScale * 1.2f * (front ? s.MuFrontScale : s.MuRearScale);
            if (!front)
            {   // rear grip lets go on the handbrake and when power is pitched into a turn
                if (car.Handbrake) mu *= 0.28f;
                else mu *= 1f - 0.38f * thr * Mathf.Abs(steerSm) * Mathf.InverseLerp(6f, 22f, speed);
            }
            float fmax = mu * fz;
            bool driven = s.Layout == DriveLayout.FWD ? front : !front;
            float driveForce = 0f;
            if (driven && thr > 0.001f)
            {
                driveForce = thr * engineForce * 0.5f * (gear < 0 ? -1f : 1f);
            }
            float brakeForce = brk * (front ? 0.62f : 0.38f) * 1.15f * s.Mass * 9.81f * 0.5f;
            if (car.Handbrake && !front) brakeForce = Mathf.Max(brakeForce, 0.5f * s.Mass * 9.81f * 0.5f);
            float fx = driveForce - brakeForce * Mathf.Sign(vf) * Mathf.Clamp01(Mathf.Abs(vf) / 0.6f);
            float fs = -vs * s.Mass * 0.25f / dt * 0.55f;                                          // drain the sideways speed of this corner's share of the mass
            fs = Mathf.Clamp(fs, -fmax, fmax);
            float mag = Mathf.Sqrt(fx * fx + fs * fs);
            if (mag > fmax && mag > 1f) { fx *= fmax / mag; fs *= fmax / mag; }
            Vector3 contact = Vector3.Lerp(new Vector3(mount.x, ground, mount.z), mount, 0.65f);           // apply low on the body: little pitch or roll from grip
            body.AddForceAtPosition(fwdW * fx + rightW * fs, contact);
            slipSum += Mathf.Abs(vs) / Mathf.Max(speed, 4f);
            car.WheelFx[i] = Mathf.Lerp(car.WheelFx[i], Mathf.Clamp01((Mathf.Abs(vs) - 2.5f) / 6f + (fx > 0.92f * fmax ? 0.6f : 0f)), 0.4f);
            car.WheelSlip[i] = mag / Mathf.Max(fmax, 1f); car.WheelKappa[i] = fx / Mathf.Max(fmax, 1f);
            spin[i] += vf / s.WheelR * dt;
        }
        car.WheelsOnGround = grounded; car.CurrentSurface = surf0;
        car.SlipAmount = Mathf.Lerp(car.SlipAmount, grounded > 0 ? Mathf.Clamp01(slipSum * 0.9f) : 0f, 0.25f);
        car.LongAccel = Mathf.Lerp(car.LongAccel, Mathf.Clamp((lv.z - prevFwd) / dt, -25f, 25f), 0.03f); prevFwd = lv.z;

        // ---- turning: the yaw rate the steering asks for is applied as a torque, so the car turns where it is pointed (weaker when sliding on purpose)
        if (grounded >= 3 && speed > 1.5f)
        {
            Vector3 la = tr.InverseTransformDirection(body.angularVelocity);
            float rDes = lv.z * Mathf.Tan(steerAngle) / s.Wheelbase * (car.Handbrake ? 1.35f : 1f);
            float rMax = 1.5f * 9.81f / Mathf.Max(speed, 1f); rDes = Mathf.Clamp(rDes, -rMax, rMax);           // the yaw rate cannot ask for more than the tyres give
            float k = car.Handbrake ? 3f : 9f;
            float torque = Mathf.Clamp((rDes - la.y) * k * body.inertiaTensor.y, -5f * body.inertiaTensor.y, 5f * body.inertiaTensor.y);
            body.AddTorque(up * torque);
        }
        // ---- aero drag and downforce, rolling resistance
        if (speed > 0.1f)
        {
            body.AddForce(-vel.normalized * (0.5f * Rho * s.CdA * speed * speed + (grounded > 0 ? 0.012f * s.Mass * 9.81f : 0f)));
            float down = 0.5f * Rho * s.ClA * speed * speed;
            body.AddForceAtPosition(-up * down * s.ClFront, tr.TransformPoint(new Vector3(0, 0, s.A)));
            body.AddForceAtPosition(-up * down * (1f - s.ClFront), tr.TransformPoint(new Vector3(0, 0, -s.B)));
        }
        car.HullContact(dt);
    }
    float prevFwd;
}
