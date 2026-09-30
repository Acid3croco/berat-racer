using UnityEngine;

/// <summary>
/// Another player's car, drawn from the states it streams (NetSession): body pose blended ~0.1 s in the past, wheels steered, dropped and spun,
/// brake / reverse lamps, and the same tyre smoke and skid marks as the local car (TyreFx reads this through IWheelFx).
/// Its body is a kinematic box: the local car bumps into it, it does not get pushed (each player owns his car).
/// </summary>
public class RemoteCar : MonoBehaviour, IWheelFx
{
    public byte Id { get; private set; }
    public int CarIndex { get; private set; }
    public string Name { get; private set; } = "";
    public CarSpec Spec { get; private set; }
    public float[] WheelFx { get; } = new float[4];
    public Vector3[] WheelPoint { get; } = new Vector3[4];
    public Surface[] SurfaceUnderWheel { get; } = new Surface[4];
    public Surface CurrentSurface { get; private set; }
    public event System.Action Respawned;
    public Vector3 Velocity { get; private set; }
    public double LastHeard => line.LastHeard;

    const float MaxExtrapolate = 0.25f;                 // s: past the newest state the car coasts on its velocity for at most this long, then holds
    readonly Timeline<CarSnap> line = new Timeline<CarSnap>();
    CarVisualRefs vis; Rigidbody rb;
    readonly float[] spin = new float[4];
    double newestT = double.NegativeInfinity; bool snap = true;

    public static RemoteCar Create(in CarSnap first, Material mat)
    {
        var spec = CarSpec.All[Mathf.Clamp(first.CarIndex, 0, CarSpec.All.Length - 1)];
        var go = new GameObject($"Remote car {first.Id} ({first.Name})");
        go.transform.SetPositionAndRotation(first.Pos, first.Rot);
        var rc = go.AddComponent<RemoteCar>();
        rc.Id = first.Id; rc.CarIndex = first.CarIndex; rc.Name = first.Name; rc.Spec = spec;
        rc.vis = CarVisual.Build(go.transform, mat, spec);
        rc.rb = go.AddComponent<Rigidbody>(); rc.rb.isKinematic = true; rc.rb.interpolation = RigidbodyInterpolation.Interpolate;
        var box = go.AddComponent<BoxCollider>();                                        // same body box as a traffic car
        float yBottom = spec.GroundY + 0.16f, yTop = spec.GroundY + spec.Height * 0.72f;
        box.center = new Vector3(0, 0.5f * (yTop + yBottom), 0.5f * (spec.ZFront + spec.ZRear)); box.size = new Vector3(spec.Width * 0.98f, yTop - yBottom, (spec.ZFront - spec.ZRear) * 0.98f);
        go.AddComponent<TyreFx>().Init(rc);
        Log.I("net", $"remote car {first.Id} '{first.Name}' ({spec.Name}) appears at {first.Pos}");
        return rc;
    }

    /// <summary>A state arrived. A new respawn count (reset, teleport, map jump) cuts the history so the car jumps instead of flying across the map.</summary>
    public void Push(in CarSnap s, double now)
    {
        if (s.T <= newestT) return;                                                        // late datagram
        newestT = s.T; Name = s.Name;
        if (!line.Empty && s.Respawns != line.Newest.Respawns) { line.Clear(); snap = true; }
        line.Add(s.T, s, now);
    }

    CarSnap Now(out double ahead)
    {
        var s = line.Sample(Time.realtimeSinceStartupAsDouble, CarSnap.Blend, out ahead);
        if (ahead > 0) s.Pos += s.Vel * (float)System.Math.Min(ahead, MaxExtrapolate);
        return s;
    }

    void FixedUpdate()
    {
        if (line.Empty) return;
        var s = Now(out _);
        if (snap)
        {
            snap = false;
            rb.position = s.Pos; rb.rotation = s.Rot; transform.SetPositionAndRotation(s.Pos, s.Rot);
            Respawned?.Invoke();
            return;
        }
        rb.MovePosition(s.Pos); rb.MoveRotation(s.Rot);
    }

    void Update()
    {
        if (line.Empty) return;
        var s = Now(out double ahead);
        Velocity = s.Vel; CurrentSurface = s.Surface;
        bool stale = ahead > MaxExtrapolate;                                               // no news: stop smoking and marking
        for (int i = 0; i < 4; i++)
        {
            var m = Spec.Mount(i); float drop = s.Drop[i], steer = i == 0 ? s.SteerL : i == 1 ? s.SteerR : 0f;
            spin[i] = Mathf.Repeat(spin[i] + s.Omega[i] * Time.deltaTime, 2f * Mathf.PI);
            vis.wheels[i].localPosition = new Vector3(m.x, -drop, m.z);
            vis.wheels[i].localRotation = Quaternion.Euler(0, steer * Mathf.Rad2Deg, 0) * Quaternion.Euler(spin[i] * Mathf.Rad2Deg, 0, 0);
            WheelPoint[i] = transform.TransformPoint(new Vector3(m.x, -drop - Spec.WheelR, m.z));
            WheelFx[i] = stale ? 0f : s.Fx[i];
            SurfaceUnderWheel[i] = s.WheelSurface(i);
        }
        vis.brakeGlow.SetActive((s.Lights & 1) != 0); vis.reverseGlow.SetActive((s.Lights & 2) != 0);
    }

    /// <summary>The local car as a state for the others.</summary>
    public static CarSnap Capture(CarController car, int carIndex, string name, byte respawns, double t)
    {
        var s = new CarSnap
        {
            CarIndex = (byte)carIndex, Respawns = respawns, Name = name, T = t,
            Pos = car.Body.position, Rot = car.Body.rotation, Vel = car.Body.linearVelocity, Surface = car.CurrentSurface,
            Lights = (byte)((car.Brake > 0.1f && car.Gear > 0 || car.Gear < 0 && car.Throttle > 0.1f ? 1 : 0) | (car.Gear < 0 ? 2 : 0)),
        };
        for (int i = 0; i < 4; i++)
        {
            car.WheelPose(i, out float drop, out float steer, out float omega);
            s.Drop[i] = drop; s.Omega[i] = omega; s.Fx[i] = car.WheelFx[i];
            if (i == 0) s.SteerL = steer; else if (i == 1) s.SteerR = steer;
            s.SurfBits |= (byte)(((int)car.SurfaceUnderWheel[i] & 3) << (2 * i));
        }
        return s;
    }
}
