using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Ambient traffic: cars that drive the real road network at the speed limit. Independent of the driving model — traffic cars are kinematic and follow the road polylines
/// (right-hand traffic, one-way roads respected, junction turns chosen at random, curves and junctions slow them down, a following distance keeps them apart).
/// Speed = min(French legal limit from BD TOPO, 1.3 x the road's average speed + 8 km/h). Cars spawn out of sight around the player and are removed when far away.
/// Online, the host simulates traffic around every player (Others) and streams it (Capture); a client's traffic is a Replica that only mirrors it (Mirror).
/// </summary>
public class Traffic : MonoBehaviour
{
    public int Target = 26;                                  // cars alive around the player
    public bool Enabled = true;
    const float SpawnMin = 220f, SpawnMax = 900f, Despawn = 1150f, LaneMax = 2.1f;

    WorldBuilder world; CarController player; Material mat;
    readonly List<Agent> agents = new List<Agent>();
    public readonly List<RemoteCar> Others = new List<RemoteCar>();          // the other players' cars (NetSession keeps the list)
    public bool Replica { get; private set; }                                  // a client's traffic: the host's vehicles, nothing simulated here
    readonly Dictionary<ushort, Agent> replicas = new Dictionary<ushort, Agent>();
    readonly ClockSync hostClock = new ClockSync();
    ushort nextId; int spawnAnchor;
    readonly System.Random rng = new System.Random(11);
    float spawnTimer, logTimer;
    public int Count => agents.Count;
    public float MeanSpeedKmh { get { if (agents.Count == 0) return 0; float s = 0; foreach (var a in agents) s += a.v; return s / agents.Count * 3.6f; } }

    static readonly Color32[] Paints =
    {
        new Color32(236, 236, 232, 255), new Color32(30, 30, 34, 255), new Color32(150, 154, 160, 255), new Color32(180, 30, 34, 255), new Color32(36, 70, 128, 255),
        new Color32(92, 106, 96, 255), new Color32(224, 220, 200, 255), new Color32(196, 120, 40, 255), new Color32(62, 62, 68, 255), new Color32(110, 20, 40, 255)
    };

    class Agent
    {
        public GameObject go; public Rigidbody rb; public VehicleParts parts; public VehicleType type;
        public ushort id; public byte lookA, lookB;                                       // look: which model and paints (see PickLook)
        public Timeline<TrafficSnap> line;                                                  // replica only
        public float speedFactor = 1f, gapTime = 1.5f, length = 4.6f, trailerYaw;
        public Rigidbody trailerRb; public readonly List<Vector3> crumbs = new List<Vector3>();
        public RoadFollower f; public float v, spin, stuck, age;
        public Vector3 pos; public Vector2 dir; public float yaw, slope;
    }

    public void Init(WorldBuilder w, CarController p)
    {
        world = w; player = p;
        if (mat == null) mat = new Material(GameAssets.Flat);
    }
    public void SetPlayer(CarController p) { player = p; }

    public void SetEnabled(bool on)
    {
        if (Replica) { Log.I("traffic", "traffic belongs to the host of the session"); return; }
        Enabled = on;
        if (!on) { foreach (var a in agents) Kill(a); agents.Clear(); }
        Log.I("traffic", on ? "traffic on" : "traffic off");
    }

    // ------------------------------------------------------------------ speed limits
    public static float LimitKmh(RoadData r) => RoadFollower.LimitKmh(r);

    // ------------------------------------------------------------------ frame
    void FixedUpdate()
    {
        if (!Enabled || world == null || !world.Ready || player == null) return;
        float dt = Time.fixedDeltaTime;
        if (Replica) { StepReplicas(dt); RefreshVehicles(); return; }
        Maintain(dt);
        RefreshVehicles();
        for (int i = 0; i < agents.Count; i++) Step(agents[i], dt);
    }

    static void Kill(Agent a) { if (a.parts != null && a.parts.trailer != null) Destroy(a.parts.trailer); if (a.go != null) Destroy(a.go); }

    // ------------------------------------------------------------------ players the traffic lives around
    readonly List<(Vector3 pos, Vector3 look)> anchors = new List<(Vector3, Vector3)>();

    /// <summary>Every player's car and where it looks: the local one (camera direction), then the others online (direction of travel).</summary>
    void RefreshAnchors()
    {
        anchors.Clear();
        var cam = Camera.main; Vector3 look = cam != null ? cam.transform.forward : player.transform.forward;
        anchors.Add((player.transform.position, Flat(look)));
        foreach (var o in Others) if (o != null) anchors.Add((o.transform.position, Flat(o.Velocity.sqrMagnitude > 1f ? o.Velocity : o.transform.forward)));
    }
    static Vector3 Flat(Vector3 v) { v.y = 0; return v.normalized; }

    float NearestPlayer(Vector3 p)
    {
        float best = float.MaxValue;
        foreach (var an in anchors) best = Mathf.Min(best, Vector2.Distance(new Vector2(p.x, p.z), new Vector2(an.pos.x, an.pos.z)));
        return best;
    }

    void Maintain(float dt)
    {
        RefreshAnchors();
        for (int i = agents.Count - 1; i >= 0; i--)
        {
            var a = agents[i]; a.age += dt;
            bool gone = NearestPlayer(a.pos) > Despawn || !world.Data.HasChunk(a.pos.x, a.pos.z) || a.stuck > 30f || float.IsNaN(a.pos.x);
            if (gone) { Kill(a); agents.RemoveAt(i); }
        }
        spawnTimer -= dt;
        if (agents.Count < Target * anchors.Count && spawnTimer <= 0f) { spawnAnchor = (spawnAnchor + 1) % anchors.Count; TrySpawn(anchors[spawnAnchor].pos); spawnTimer = 0.3f / anchors.Count; }
        logTimer -= dt;
        if (logTimer <= 0f)
        {
            logTimer = 20f; int[] n = new int[4]; float sf = 0, sf2 = 0; foreach (var g in agents) { n[(int)g.type]++; sf += g.speedFactor; sf2 += g.speedFactor * g.speedFactor; }
            float mean = agents.Count > 0 ? sf / agents.Count : 0f, sd = agents.Count > 1 ? Mathf.Sqrt(Mathf.Max(0f, sf2 / agents.Count - mean * mean)) : 0f;
            Log.I("traffic", $"{agents.Count} vehicles (cars {n[0]}, vans {n[1]}, trucks {n[2]}, semis {n[3]}), mean speed {MeanSpeedKmh:F0} km/h, driver speed factor {mean:F2} +- {sd:F2}");
        }
    }

    /// <summary>A road point around `pp` that no player is close to, nor looking at within 600 m.</summary>
    void TrySpawn(Vector3 pp)
    {
        var roads = world.Data.Roads; if (roads.Length == 0) return;
        for (int attempt = 0; attempt < 40; attempt++)
        {
            var r = roads[rng.Next(roads.Length)];
            if (r.dirt || r.bridge || r.hw < 1.9f || r.Length < 40f || r.imp == "6") continue;
            float s = (float)(0.15 + 0.7 * rng.NextDouble()) * r.Length;
            Vector3 p = r.At(s, out Vector2 dirInc);
            Vector3 d3 = p - pp; d3.y = 0;
            if (d3.magnitude > SpawnMax || NearestPlayer(p) < SpawnMin) continue;
            bool seen = false;
            foreach (var an in anchors) { Vector3 e = p - an.pos; e.y = 0; if (e.magnitude < 600f && Vector3.Dot(e.normalized, an.look) > 0.2f) { seen = true; break; } }
            if (seen) continue;                                                                       // not popping in ahead of a player
            bool tooClose = false; foreach (var o in agents) if ((o.pos - p).sqrMagnitude < 35f * 35f) { tooClose = true; break; }
            if (tooClose) continue;
            bool fwd = r.oneway == 1 ? true : r.oneway == 2 ? false : rng.Next(2) == 0;
            SpawnAt(r, s, fwd); return;
        }
    }

    // ------------------------------------------------------------------ who drives here
    static float Normal(System.Random rng, float mean, float sd)
    {
        double u1 = 1.0 - rng.NextDouble(), u2 = rng.NextDouble();
        return mean + sd * (float)(System.Math.Sqrt(-2.0 * System.Math.Log(u1)) * System.Math.Cos(2.0 * System.Math.PI * u2));
    }

    /// <summary>Vehicle mix by road class, after French traffic counts: heavy goods vehicles are ~15% of motorway traffic, ~3% on rural roads, ~1% in town; vans ~10-12%; the rest are cars.</summary>
    VehicleType PickType(RoadData r)
    {
        float lim = r.limit > 0 ? r.limit : LimitKmh(r);
        float semi, truck, van;
        if (r.kind == 2 || r.kind == 3 || lim >= 110f) { semi = 0.13f; truck = 0.03f; van = 0.10f; }
        else if (lim >= 70f) { semi = 0.025f; truck = 0.02f; van = 0.11f; }
        else { semi = 0.004f; truck = 0.02f; van = 0.10f; }
        if (r.hw < 2.5f) { semi = 0f; truck = 0f; }                                            // trucks stay off narrow roads (< ~4.3 m)
        else if (r.hw < 3.0f) semi *= 0.3f;
        float x = (float)rng.NextDouble();
        if ((x -= semi) < 0f) return VehicleType.Semi;
        if ((x -= truck) < 0f) return VehicleType.Truck;
        if ((x -= van) < 0f) return VehicleType.Van;
        return VehicleType.Car;
    }

    static readonly Color32[] HaulPaints = { new Color32(238, 238, 238, 255), new Color32(238, 238, 238, 255), new Color32(30, 60, 130, 255), new Color32(190, 30, 30, 255), new Color32(200, 204, 208, 255), new Color32(40, 110, 60, 255) };
    static readonly Color32[] VanPaints = { new Color32(240, 240, 240, 255), new Color32(240, 240, 240, 255), new Color32(240, 240, 240, 255), new Color32(200, 204, 208, 255), new Color32(30, 60, 130, 255), new Color32(190, 30, 30, 255), new Color32(60, 62, 66, 255) };

    void SpawnAt(RoadData r, float s, bool fwd)
    {
        var type = PickType(r);
        var a = new Agent { type = type, id = nextId++ }; a.f = new RoadFollower(world.Data, rng); a.f.Place(r, s, fwd);
        PickLook(type, out a.lookA, out a.lookB);
        Dress(a);
        // this driver: speed relative to the limit is normally distributed (a few slow, a few fast); lorries are speed-limited and steadier
        bool heavy = type == VehicleType.Truck || type == VehicleType.Semi;
        a.speedFactor = heavy ? Mathf.Clamp(Normal(rng, 0.97f, 0.04f), 0.85f, 1.03f) : Mathf.Clamp(Normal(rng, 1.02f, 0.09f), 0.68f, 1.30f);
        a.gapTime = Mathf.Clamp(Normal(rng, 1.6f, 0.4f), 0.8f, 2.6f);
        a.v = LimitKmh(r) / 3.6f * a.speedFactor * 0.85f;
        agents.Add(a);
        Pose(a, 0f, true);
        a.go.transform.SetPositionAndRotation(a.pos, Quaternion.Euler(0, a.yaw, 0));
        a.crumbs.Clear(); a.crumbs.Add(a.pos - new Vector3(a.dir.x, 0, a.dir.y) * 40f); a.crumbs.Add(a.pos);
        if (a.parts.trailer != null) PoseTrailer(a);
    }

    /// <summary>Model and paints, as two small numbers a client rebuilds the same vehicle from. Cars: A = model (hatch or saloon; the GT3 is the player's toy), B = paint.</summary>
    void PickLook(VehicleType type, out byte a, out byte b)
    {
        switch (type)
        {
            case VehicleType.Van: a = (byte)rng.Next(VanPaints.Length); b = 0; break;
            case VehicleType.Truck: case VehicleType.Semi: a = (byte)rng.Next(HaulPaints.Length); b = (byte)rng.Next(HaulPaints.Length); break;
            default: a = (byte)(rng.Next(100) < 58 ? 0 : 1); b = (byte)rng.Next(Paints.Length); break;
        }
    }

    /// <summary>Builds the vehicle's meshes, kinematic body and collision boxes from its type and look.</summary>
    void Dress(Agent a)
    {
        var type = a.type; VehicleParts parts;
        switch (type)
        {
            case VehicleType.Van: parts = TrafficVisual.Van(mat, VanPaints[a.lookA % VanPaints.Length]); break;
            case VehicleType.Truck: parts = TrafficVisual.BoxTruck(mat, HaulPaints[a.lookA % HaulPaints.Length], HaulPaints[a.lookB % HaulPaints.Length]); break;
            case VehicleType.Semi: parts = TrafficVisual.Semi(mat, HaulPaints[a.lookA % HaulPaints.Length], HaulPaints[a.lookB % HaulPaints.Length]); break;
            default:
                {
                    var spec = CarSpec.All[a.lookA == 0 ? 0 : 1].WithPaint(Paints[a.lookB % Paints.Length]);
                    var go0 = new GameObject("traffic car"); var vis = CarVisual.Build(go0.transform, mat, spec);
                    vis.brakeGlow.SetActive(false); vis.reverseGlow.SetActive(false);
                    parts = new VehicleParts { root = go0, wheels = vis.wheels, wheelRadius = new[] { spec.WheelR, spec.WheelR, spec.WheelR, spec.WheelR }, length = spec.Length, width = spec.Width, groundOffset = spec.WheelRadiusSum, isCar = true, carSpec = spec };
                    break;
                }
        }
        a.parts = parts; a.go = parts.root; a.length = parts.length + (type == VehicleType.Semi ? 12.5f : 0f);
        a.rb = a.go.AddComponent<Rigidbody>(); a.rb.isKinematic = true; a.rb.interpolation = RigidbodyInterpolation.Interpolate;
        var box = a.go.AddComponent<BoxCollider>();
        if (parts.isCar)
        {
            var spec = parts.carSpec; float bodyMid = 0.5f * (spec.ZFront + spec.ZRear), bodyLen = spec.ZFront - spec.ZRear, yBottom = spec.GroundY + 0.16f, yTop = spec.GroundY + spec.Height * 0.72f;
            box.center = new Vector3(0, 0.5f * (yTop + yBottom), bodyMid); box.size = new Vector3(spec.Width * 0.98f, yTop - yBottom, bodyLen * 0.98f);
        }
        else
        {
            float h = type == VehicleType.Van ? 1.9f : 3.4f;
            box.center = new Vector3(0, 0.3f + h * 0.5f, 0); box.size = new Vector3(parts.width, h, type == VehicleType.Semi ? 6.0f : parts.length);
        }
        if (parts.trailer != null)
        {
            a.trailerRb = parts.trailer.AddComponent<Rigidbody>(); a.trailerRb.isKinematic = true; a.trailerRb.interpolation = RigidbodyInterpolation.Interpolate;
            var tbox = parts.trailer.AddComponent<BoxCollider>(); tbox.center = new Vector3(0, 2.4f, 0); tbox.size = new Vector3(2.5f, 2.7f, 13.4f);
            parts.trailer.transform.SetParent(a.go.transform.parent, true);                      // independent of the tractor's transform
        }
        if (parts.isCar) for (int i = 0; i < parts.wheels.Length; i++) { var m = parts.carSpec.Mount(i); parts.wheels[i].localPosition = new Vector3(m.x, -0.40f, m.z); }
    }

    // ------------------------------------------------------------------ one car
    void Step(Agent a, float dt)
    {
        if (a.go == null) return;
        var f = a.f; f.EnsurePlanned();

        // target speed: legal limit x this driver's habit, then bends, junctions, the car in front
        float lim = RoadFollower.LimitKmh(f.road, f.fwd);
        if (a.type == VehicleType.Truck || a.type == VehicleType.Semi) lim = Mathf.Min(lim, 90f);                    // heavy goods vehicles: 90 km/h at most
        float vt = lim / 3.6f * a.speedFactor;
        vt = Mathf.Min(vt, f.CurveSpeed() * (a.type == VehicleType.Car ? 1f : 0.8f), f.JunctionSpeed(vt));
        if (FindLeader(f, new Vector2(a.pos.x, a.pos.z), a.dir, a.length, a, true, out float gap, out float lv))
        {
            float want = 5f + a.v * a.gapTime;
            if (gap < want) vt = Mathf.Min(vt, Mathf.Max(0f, lv - (want - gap) * 0.6f));
            if (gap < 4f) vt = 0f;
        }
        a.v = Mathf.MoveTowards(a.v, vt, (vt > a.v ? 2.6f : 6.5f) * dt);
        a.stuck = a.v < 0.3f ? a.stuck + dt : 0f;
        f.Move(a.v * dt);
        if (f.DeadEnd && f.road.oneway != 0 && f.ToEnd < 1f) a.stuck = 99f;                                          // one-way dead end: remove
        Pose(a, dt, false);
        a.rb.MovePosition(a.pos); a.rb.MoveRotation(Quaternion.Euler(-a.slope, a.yaw, 0f));
        // breadcrumbs of the cab's path: the trailer follows them (so it cuts corners like a real semi)
        if (Vector3.SqrMagnitude(a.pos - a.crumbs[a.crumbs.Count - 1]) > 0.25f) { a.crumbs.Add(a.pos); if (a.crumbs.Count > 200) a.crumbs.RemoveRange(0, 60); }
        if (a.parts.trailer != null) PoseTrailer(a);
        float ang = a.v * dt; a.spin += ang;
        SpinWheels(a);
    }

    static void SpinWheels(Agent a)
    {
        var parts = a.parts;
        for (int i = 0; i < parts.wheels.Length; i++) parts.wheels[i].localRotation = Quaternion.Euler(a.spin / parts.wheelRadius[i] * Mathf.Rad2Deg, 0, 0);
        for (int i = 0; i < parts.trailerWheels.Length; i++) parts.trailerWheels[i].localRotation = Quaternion.Euler(a.spin / parts.trailerWheelRadius[i] * Mathf.Rad2Deg, 0, 0);
    }

    /// <summary>Point on the cab's recent path `back` metres behind its current position.</summary>
    static Vector3 PointBehind(Agent a, float back)
    {
        float remaining = back; Vector3 prev = a.pos;
        for (int i = a.crumbs.Count - 1; i >= 0; i--)
        {
            Vector3 c = a.crumbs[i]; float seg = Vector3.Distance(prev, c);
            if (seg >= remaining && seg > 1e-4f) return Vector3.Lerp(prev, c, remaining / seg);
            remaining -= seg; prev = c;
        }
        return prev;
    }

    void PoseTrailer(Agent a)
    {
        Vector3 K = PointBehind(a, a.parts.kingpin), R = PointBehind(a, a.parts.kingpin + a.parts.trailerReach);
        Vector3 axis = K - R; Vector3 flat = new Vector3(axis.x, 0, axis.z); if (flat.sqrMagnitude < 0.01f) flat = new Vector3(a.dir.x, 0, a.dir.y);
        Vector3 fwd = axis.sqrMagnitude > 0.01f ? axis.normalized : flat.normalized;
        Vector3 origin = K - fwd * 5.2f;                                                        // trailer centre: kingpin is 5.2 m in front of it
        a.trailerRb.MovePosition(origin); a.trailerRb.MoveRotation(Quaternion.LookRotation(fwd, Vector3.up));
    }

    /// <summary>Position and heading from the road: right-hand lane, ground = the road surface.</summary>
    void Pose(Agent a, float dt, bool snap)
    {
        var f = a.f; Vector3 p = f.road.At(f.s, out Vector2 dInc);
        Vector2 dir = f.fwd ? dInc : -dInc;
        float laneT = RoadFollower.LaneOffset(f.road);
        f.lane = snap ? laneT : Mathf.MoveTowards(f.lane, laneT, 1.5f * dt);
        Vector2 right = new Vector2(dir.y, -dir.x);
        // heading looks a few metres ahead so the car follows curves smoothly
        Vector3 ahead = f.road.At(f.s + (f.fwd ? 4f : -4f), out _);
        Vector2 toAhead = new Vector2(ahead.x - p.x, ahead.z - p.z);
        float yawT = toAhead.sqrMagnitude > 0.01f ? Mathf.Atan2(toAhead.x, toAhead.y) * Mathf.Rad2Deg : Mathf.Atan2(dir.x, dir.y) * Mathf.Rad2Deg;
        a.yaw = snap ? yawT : Mathf.MoveTowardsAngle(a.yaw, yawT, 140f * dt);
        a.dir = dir;
        a.pos = new Vector3(p.x + right.x * f.lane, p.y + ChunkMeshes.RoadLift + a.parts.groundOffset, p.z + right.y * f.lane);
        a.slope = toAhead.magnitude > 0.1f ? Mathf.Atan2(ahead.y - p.y, toAhead.magnitude) * Mathf.Rad2Deg : 0f;
    }

    // ------------------------------------------------------------------ seeing other vehicles
    struct Veh { public Vector2 pos, dir; public float v, len; public object id; }
    readonly List<Veh> vehs = new List<Veh>();

    void RefreshVehicles()
    {
        vehs.Clear();
        foreach (var o in agents) vehs.Add(new Veh { pos = new Vector2(o.pos.x, o.pos.z), dir = o.dir, v = o.v, len = o.length, id = o });
        if (player != null)
        {
            Vector3 pv = player.Body.linearVelocity; float ps = new Vector2(pv.x, pv.z).magnitude;
            Vector2 pd = ps > 1f ? new Vector2(pv.x, pv.z) / ps : new Vector2(player.transform.forward.x, player.transform.forward.z).normalized;
            vehs.Add(new Veh { pos = new Vector2(player.transform.position.x, player.transform.position.z), dir = pd, v = ps, len = 4.5f, id = player });
        }
        foreach (var o in Others)
        {
            if (o == null) continue;
            Vector3 ov = o.Velocity; float os = new Vector2(ov.x, ov.z).magnitude;
            Vector2 od = os > 1f ? new Vector2(ov.x, ov.z) / os : new Vector2(o.transform.forward.x, o.transform.forward.z).normalized;
            vehs.Add(new Veh { pos = new Vector2(o.transform.position.x, o.transform.position.z), dir = od, v = os, len = 4.5f, id = o });
        }
    }

    // ------------------------------------------------------------------ online
    /// <summary>Host: every vehicle within `range` of `near`, as the host sees it now.</summary>
    public void Capture(Vector3 near, float range, List<TrafficSnap> into)
    {
        into.Clear();
        if (!Enabled || Replica) return;
        foreach (var a in agents)
        {
            if (a.go == null || Vector2.Distance(new Vector2(a.pos.x, a.pos.z), new Vector2(near.x, near.z)) > range) continue;
            var s = new TrafficSnap { Id = a.id, Type = a.type, LookA = a.lookA, LookB = a.lookB, Pos = a.pos, Yaw = a.yaw, Slope = a.slope, V = a.v };
            if (a.trailerRb != null) { var e = a.trailerRb.rotation.eulerAngles; s.TrailerPos = a.trailerRb.position; s.TrailerYaw = e.y; s.TrailerPitch = e.x; }
            into.Add(s);
        }
    }

    /// <summary>Client: stop simulating and mirror the host's vehicles (on), or go back to our own traffic (off).</summary>
    public void SetReplica(bool on)
    {
        if (on == Replica) return;
        foreach (var a in agents) Kill(a);
        agents.Clear(); replicas.Clear();
        Replica = on; Enabled = true;
        Log.I("traffic", on ? "mirroring the host's traffic" : "own traffic again");
    }

    /// <summary>Client: one vehicle from the host. The first time an id is seen the vehicle is built from its type and look.</summary>
    public void Mirror(in TrafficSnap s, double senderT, double now)
    {
        if (!Replica || mat == null) return;
        if (!replicas.TryGetValue(s.Id, out var a))
        {
            a = new Agent { type = s.Type, id = s.Id, lookA = s.LookA, lookB = s.LookB, line = new Timeline<TrafficSnap>(hostClock) };
            Dress(a);
            a.pos = s.Pos; a.yaw = s.Yaw; a.go.transform.SetPositionAndRotation(s.Pos, Quaternion.Euler(-s.Slope, s.Yaw, 0f));
            if (a.trailerRb != null) a.parts.trailer.transform.SetPositionAndRotation(s.TrailerPos, Quaternion.Euler(s.TrailerPitch, s.TrailerYaw, 0f));
            replicas.Add(s.Id, a); agents.Add(a);
        }
        a.line.Add(senderT, s, now);
    }

    void StepReplicas(float dt)
    {
        double now = Time.realtimeSinceStartupAsDouble;
        for (int i = agents.Count - 1; i >= 0; i--)
        {
            var a = agents[i];
            if (now - a.line.LastHeard > 1.5) { Kill(a); agents.RemoveAt(i); replicas.Remove(a.id); continue; }      // gone on the host, or out of our range
            var s = a.line.Sample(now, TrafficSnap.Blend, out _);
            a.pos = s.Pos; a.yaw = s.Yaw; a.slope = s.Slope; a.v = s.V;
            float yr = s.Yaw * Mathf.Deg2Rad; a.dir = new Vector2(Mathf.Sin(yr), Mathf.Cos(yr));
            a.rb.MovePosition(s.Pos); a.rb.MoveRotation(Quaternion.Euler(-s.Slope, s.Yaw, 0f));
            if (a.trailerRb != null) { a.trailerRb.MovePosition(s.TrailerPos); a.trailerRb.MoveRotation(Quaternion.Euler(s.TrailerPitch, s.TrailerYaw, 0f)); }
            a.spin += a.v * dt; SpinWheels(a);
        }
    }

    /// <summary>
    /// What is in the way along MY lane path (looked at every 4 m up to ~45 m, bends and junctions included)? Another vehicle heading the same way is a leader to keep a gap to;
    /// a stopped one is an obstacle; one crossing my path is given way to when it comes from my right (priorité à droite).
    /// Oncoming vehicles in the opposite lane are not in the way. `self` is skipped (agent or the player's car).
    /// </summary>
    public bool FindLeader(RoadFollower f, Vector2 pos, Vector2 dirSelf, float lengthSelf, object self, bool includePlayer, out float gap, out float speed)
    {
        float bestGap = 1e9f, bestSpeed = 0f; Vector2 right = new Vector2(dirSelf.y, -dirSelf.x);
        float reach = 46f;
        for (float dist = 4f; dist <= reach; dist += 4f)
        {
            Vector3 q = f.Ahead(dist, out Vector2 tdir); Vector2 qp = new Vector2(q.x, q.z);
            foreach (var o in vehs)
            {
                if (ReferenceEquals(o.id, self)) continue;
                if (!includePlayer && o.id == (object)player) continue;
                float lateral = Vector2.Distance(qp, o.pos);
                float reachOther = 1.35f + 0.5f * Mathf.Min(o.len, 6f) * Mathf.Abs(Vector2.Dot(o.dir, tdir)) * 0.6f;      // half a width, plus a bit of length when it points along the path
                if (lateral > 1.15f + reachOther) continue;
                float align = Vector2.Dot(o.dir, tdir);
                float g;
                if (align > 0.5f || o.v < 0.6f)
                {   // same direction (or stopped): keep a gap to it
                    Vector2 d = o.pos - pos; if (Vector2.Dot(d, dirSelf) < 0.5f) continue;
                    g = dist - 0.5f * (lengthSelf + o.len);
                    if (g < bestGap) { bestGap = g; bestSpeed = o.v * Mathf.Max(0f, align); }
                }
                else if (align < -0.5f) continue;                                                        // oncoming in its own lane
                else
                {   // crossing my path: priority to the right
                    float lat = Vector2.Dot(o.pos - pos, right);
                    if (lat > 0.5f && Vector2.Dot(o.pos - pos, dirSelf) > -2f) { g = dist - 3f; if (g < bestGap) { bestGap = g; bestSpeed = 0f; } }
                }
            }
            if (bestGap < 1e8f) break;                                                                      // the nearest obstacle along the path decides
        }
        gap = bestGap; speed = bestSpeed;
        return bestGap < 1e8f;
    }
}
