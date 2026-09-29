using UnityEngine;

/// <summary>
/// The stock Unity vehicle physics (PhysX WheelCollider) on our chassis, as a baseline to compare against. The world has no terrain colliders, so a small mesh patch of the
/// ground under the car (from World.GroundHeight) is rebuilt as the car moves. Engine: our torque curve with an automatic gearbox; everything else is Unity's.
/// </summary>
public class WheelColliderModel : DrivingModel
{
    public override string Name => "WheelCollider";
    public override string Info => "stock Unity WheelCollider on a moving ground patch";

    const int Cells = 40; const float Cell = 2f, Half = Cells * Cell * 0.5f;
    WheelCollider[] wc = new WheelCollider[4];
    GameObject patch; MeshCollider patchCol; Mesh patchMesh; Vector3 patchCentre = new Vector3(1e9f, 0, 0);
    int gear = 1; float rpm, load, shiftT; bool shifting;
    public override float Rpm => rpm;
    public override int Gear => gear;
    public override float Load => load;
    public override bool Shifting => shifting;

    public override void Attach(CarController host)
    {
        base.Attach(host);
        var s = car.Spec;
        patch = new GameObject("GroundPatch"); patchMesh = new Mesh { name = "groundPatch", indexFormat = UnityEngine.Rendering.IndexFormat.UInt32 };
        patchCol = patch.AddComponent<MeshCollider>(); patchCol.sharedMesh = patchMesh;
        for (int i = 0; i < 4; i++)
        {
            var go = new GameObject("wc" + i); go.transform.SetParent(car.transform, false);
            go.transform.localPosition = s.Mount(i) + Vector3.down * (0.40f - 0.12f);                 // rest wheel centre 0.40 m below the mount plane, mid-travel
            var c = go.AddComponent<WheelCollider>();
            c.radius = s.WheelR; c.mass = 18f; c.suspensionDistance = 0.24f; c.forceAppPointDistance = 0f;
            float share = (i < 2 ? s.FrontWeight : 1f - s.FrontWeight) * 0.5f * s.Mass;
            float f = i < 2 ? s.RideFreqF : s.RideFreqR;
            var sp = new JointSpring { spring = share * Mathf.Pow(2f * Mathf.PI * f, 2f), damper = 0.35f * 2f * Mathf.Sqrt(share * Mathf.Pow(2f * Mathf.PI * f, 2f) * share), targetPosition = 0.5f };
            c.suspensionSpring = sp;
            c.forwardFriction = new WheelFrictionCurve { extremumSlip = 0.4f, extremumValue = 1f, asymptoteSlip = 0.8f, asymptoteValue = 0.75f, stiffness = 1.6f };
            c.sidewaysFriction = new WheelFrictionCurve { extremumSlip = 0.25f, extremumValue = 1f, asymptoteSlip = 0.5f, asymptoteValue = 0.75f, stiffness = 1.9f };
            wc[i] = c;
        }
    }

    public override void Detach()
    {
        for (int i = 0; i < 4; i++) if (wc[i] != null) Object.Destroy(wc[i].gameObject);
        if (patch != null) Object.Destroy(patch);
    }

    public override void OnReset() { gear = 1; rpm = car.Spec.Idle; patchCentre = new Vector3(1e9f, 0, 0); }

    void RefreshPatch()
    {
        Vector3 p = car.transform.position;
        if ((new Vector2(p.x, p.z) - new Vector2(patchCentre.x, patchCentre.z)).sqrMagnitude < 36f) return;
        patchCentre = new Vector3(Mathf.Round(p.x / Cell) * Cell, 0, Mathf.Round(p.z / Cell) * Cell);
        int n = Cells + 1; var v = new Vector3[n * n]; var tri = new int[Cells * Cells * 6];
        for (int j = 0; j < n; j++) for (int i = 0; i < n; i++)
        {
            float x = patchCentre.x - Half + i * Cell, z = patchCentre.z - Half + j * Cell;
            v[j * n + i] = new Vector3(x, car.World.GroundHeight(x, z, p.y + 1f, out _) - 0.03f, z);
        }
        int t = 0;
        for (int j = 0; j < Cells; j++) for (int i = 0; i < Cells; i++)
        {
            int a = j * n + i, b = a + 1, c = a + n, d = c + 1;
            tri[t++] = a; tri[t++] = c; tri[t++] = b; tri[t++] = b; tri[t++] = c; tri[t++] = d;      // facing up
        }
        patchMesh.Clear(); patchMesh.vertices = v; patchMesh.triangles = tri;
        patchCol.sharedMesh = null; patchCol.sharedMesh = patchMesh;
    }

    public override void FixedStep(float dt)
    {
        var s = car.Spec; var body = car.Body; var tr = car.transform;
        RefreshPatch();
        float fwd = Vector3.Dot(body.linearVelocity, tr.forward), speed = body.linearVelocity.magnitude;

        // pedals: brake at standstill selects reverse
        bool reverse = fwd < 0.8f && car.Brake > 0.05f && car.Throttle < 0.05f;
        float thr = reverse ? car.Brake : car.Throttle, brk = reverse ? 0f : car.Brake;
        if (fwd < -0.5f && car.Throttle > 0.05f) { brk = car.Throttle; thr = 0f; }

        // wheel rpm of the driven wheels -> engine rpm, gear
        int d0 = s.Layout == DriveLayout.FWD ? 0 : 2;
        float wheelRpm = Mathf.Abs((wc[d0].rpm + wc[d0 + 1].rpm) * 0.5f);
        shiftT -= dt; shifting = shiftT > 0f;
        if (!shifting)
        {
            if (fwd < -0.5f) gear = -1;
            else
            {
                if (gear < 1) gear = 1;
                if (gear < s.Ratios.Length && wheelRpm * s.Ratios[gear - 1] * s.FinalDrive > s.Redline * 0.9f) { gear++; shiftT = 0.25f; }
                else if (gear > 1 && wheelRpm * s.Ratios[gear - 2] * s.FinalDrive < s.Redline * 0.75f) { gear--; shiftT = 0.25f; }
            }
        }
        float ratio = (gear > 0 ? s.Ratios[gear - 1] : s.ReverseRatio) * s.FinalDrive;
        rpm = Mathf.Lerp(rpm, Mathf.Clamp(wheelRpm * ratio, s.Idle, s.Redline), 0.3f);
        float tq = s.TorqueAt(rpm) * thr * (shifting || rpm >= s.Redline * 0.99f ? 0f : 1f) * s.Efficiency * ratio;   // N.m at the wheels, all driven wheels together
        load = Mathf.Lerp(load, thr, 0.3f);
        float steer = car.Steer * car.MaxSteerRad(speed) * Mathf.Rad2Deg;

        int grounded = 0; float slip = 0f;
        for (int i = 0; i < 4; i++)
        {
            var c = wc[i]; bool front = i < 2;
            c.steerAngle = front ? Mathf.MoveTowards(c.steerAngle, steer, 300f * dt) : 0f;
            bool driven = s.Layout == DriveLayout.FWD ? front : !front;
            c.motorTorque = driven ? (gear < 0 ? -tq : tq) * 0.5f * (gear < 0 ? 1f : 1f) : 0f;
            float bk = brk * (front ? s.BrakeF : s.BrakeR);
            if (car.Handbrake && !front) bk = Mathf.Max(bk, s.HandbrakeTq);
            c.brakeTorque = bk;
            bool hit = c.GetGroundHit(out WheelHit h);
            if (hit) { grounded++; car.WheelLoad[i] = h.force; car.WheelPoint[i] = h.point; slip += Mathf.Abs(h.sidewaysSlip); }
            else car.WheelLoad[i] = 0f;
            car.WheelFx[i] = hit ? Mathf.Lerp(car.WheelFx[i], Mathf.Clamp01((Mathf.Abs(h.sidewaysSlip) - 0.35f) * 2f + (Mathf.Abs(h.forwardSlip) - 0.5f)), 0.4f) : 0f;
            car.WheelSlip[i] = hit ? Mathf.Max(Mathf.Abs(h.sidewaysSlip), Mathf.Abs(h.forwardSlip)) : 0f; car.WheelKappa[i] = hit ? h.forwardSlip : 0f;
            car.SurfaceUnderWheel[i] = Surface.Asphalt;
            c.GetWorldPose(out Vector3 pos, out Quaternion rot);
            car.WheelVisuals[i].position = pos; car.WheelVisuals[i].rotation = rot;
        }
        car.WheelsOnGround = grounded; car.CurrentSurface = Surface.Asphalt;
        car.SlipAmount = Mathf.Lerp(car.SlipAmount, grounded > 0 ? Mathf.Clamp01(slip * 0.5f) : 0f, 0.25f);
        car.LongAccel = Mathf.Lerp(car.LongAccel, Mathf.Clamp((fwd - prevFwd) / dt, -25f, 25f), 0.03f); prevFwd = fwd;
        if (speed > 0.1f) body.AddForce(-body.linearVelocity.normalized * 0.5f * 1.2f * s.CdA * speed * speed);
        car.HullContact(dt);
    }
    float prevFwd;
}
