using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// The player's autopilot. On the road network it is the same driver as the traffic (RoadFollower): it holds the right-hand lane, obeys the French speed limit,
/// slows for bends and junctions, picks a random exit at each junction and keeps its distance to the car in front; only the vehicle differs (real steering and pedals here,
/// kinematic movement there). A test can also hand it a fixed polyline to follow (FollowPolyline).
/// </summary>
public class Autopilot
{
    readonly WorldData data; readonly CarController car;
    readonly List<Vector2> path = new List<Vector2>();          // only for FollowPolyline
    readonly System.Random rng = new System.Random(5);
    RoadFollower follower; bool custom;
    public float TargetKmh = 130f;                              // upper bound; on the road network the limit is usually lower
    public bool ObeyLimits = true;                              // false: drive at TargetKmh regardless of the road (benchmarks)
    public float SpeedFactor = 1f;                              // habit relative to the limit (1 = exactly at it)
    public static Traffic TrafficRef;                           // the car in front is found through the traffic system
    float stuckT, reverseT;
    public string Status = "";
    public int StuckEvents, TurnArounds, Relocations; readonly System.Collections.Generic.Queue<float> turnTimes = new System.Collections.Generic.Queue<float>(); public RoadFollower Follower => follower;

    public Autopilot(WorldData d, CarController c) { data = d; car = c; Reset(); }

    /// <summary>Follow exactly this polyline (used by tests that must cross a specific structure); afterwards it returns to the road network.</summary>
    public void FollowPolyline(IList<Vector2> pts) { path.Clear(); path.AddRange(pts); custom = true; }

    public void Reset()
    {
        custom = false; path.Clear();
        Vector2 p = new Vector2(car.transform.position.x, car.transform.position.z);
        Vector2 fwd = new Vector2(car.transform.forward.x, car.transform.forward.z).normalized;
        follower = new RoadFollower(data, rng);
        if (!follower.SnapTo(p, fwd)) follower = null;
    }

    static float DistToSeg(Vector2 p, Vector2 a, Vector2 b)
    {
        Vector2 ab = b - a; float t = Mathf.Clamp01(Vector2.Dot(p - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
        return (p - (a + ab * t)).magnitude;
    }

    public void Drive(float dt)
    {
        Vector2 p = new Vector2(car.transform.position.x, car.transform.position.z);
        Vector2 fwd = new Vector2(car.transform.forward.x, car.transform.forward.z).normalized;
        float speedKmh = car.SpeedKmh;
        if (custom) { DrivePolyline(p, fwd, speedKmh, dt); return; }
        if (follower == null) { Reset(); if (follower == null) { car.Throttle = 0f; car.Brake = 0.3f; Status = "no road nearby"; return; } }

        float off = follower.Track(p);
        if (off > 30f) { Status = "off-road, re-acquiring"; Reset(); return; }
        follower.lane = Mathf.MoveTowards(follower.lane, RoadFollower.LaneOffset(follower.road), 1.5f * dt);

        if (follower.DeadEnd && follower.ToEnd < 9f && speedKmh < 12f)
        {   // the road ends here (a driveway, a courtyard): stop and turn round rather than nose into the wall
            follower.Flip();
            follower.Ahead(3f, out Vector2 back);
            TurnArounds++; turnTimes.Enqueue(Time.time); while (turnTimes.Count > 0 && Time.time - turnTimes.Peek() > 25f) turnTimes.Dequeue();
            if (turnTimes.Count >= 3) { Relocate(p); turnTimes.Clear(); return; }
            Log.I("auto", $"road ends at ({p.x:F0},{p.y:F0}): turning round");
            car.Respawn(car.transform.position + Vector3.up * 0.1f, Mathf.Atan2(back.x, back.y) * Mathf.Rad2Deg);
            return;
        }
        {   // the car points against the direction of its lane (it ran into a dead end and the follower turned round): a narrow road has no room for a U-turn, so turn the car on the spot
            follower.Ahead(3f, out Vector2 laneDir);
            if (Vector2.Dot(fwd, laneDir) < -0.3f && speedKmh < 30f)
            {
                float heading = Mathf.Atan2(laneDir.x, laneDir.y) * Mathf.Rad2Deg;
                TurnArounds++; turnTimes.Enqueue(Time.time); while (turnTimes.Count > 0 && Time.time - turnTimes.Peek() > 25f) turnTimes.Dequeue();
                if (turnTimes.Count >= 3) { Relocate(p); turnTimes.Clear(); return; }                 // ping-ponging inside a cut-off stretch of road: go somewhere else
                Log.I("auto", $"dead end at ({p.x:F0},{p.y:F0}): turning round");
                car.Respawn(car.transform.position + Vector3.up * 0.1f, heading);
                return;
            }
        }
        float look = 4.5f + speedKmh * 0.115f;                                     // short enough to stay in the lane through bends (a long aim point cuts the inside of every curve)
        Vector3 tp = follower.Ahead(look, out Vector2 tdir);
        float ang = Vector2.SignedAngle(new Vector2(tp.x, tp.z) - p, fwd);         // + = target is to the right of forward
        car.Steer = Mathf.Clamp(ang / 24f, -1f, 1f);

        // speed: the road's limit (x habit), bends, junctions, the car in front
        float limit = ObeyLimits ? RoadFollower.LimitKmh(follower.road) * SpeedFactor : 999f;
        float want = Mathf.Min(TargetKmh, limit);
        float wantMs = Mathf.Min(want / 3.6f, follower.CurveSpeed());
        wantMs = Mathf.Min(wantMs, follower.JunctionSpeed(wantMs));
        if (TrafficRef != null && TrafficRef.FindLeader(follower, p, fwd, 4.5f, car, false, out float gap, out float lv))
        {
            float v = speedKmh / 3.6f, wantGap = 5f + v * 1.6f;
            if (gap < wantGap) wantMs = Mathf.Min(wantMs, Mathf.Max(0f, lv - (wantGap - gap) * 0.6f));
            if (gap < 4f) wantMs = 0f;
        }
        want = wantMs * 3.6f;
        car.Throttle = Mathf.Clamp01((want - speedKmh) / 10f);
        car.Brake = speedKmh > want + 8f ? Mathf.Clamp01((speedKmh - want - 8f) / 15f) : (want < 0.5f && speedKmh > 1f ? 0.6f : 0f);
        car.Handbrake = false;

        // stuck? reverse a bit, then re-acquire
        if (reverseT > 0) { reverseT -= dt; car.Throttle = 0; car.Brake = 1; car.Steer = -car.Steer; if (reverseT <= 0) Reset(); return; }
        stuckT = speedKmh < 2f && want > 15f ? stuckT + dt : 0f;
        if (stuckT > 3f) { stuckT = 0; reverseT = 1.6f; StuckEvents++; DumpContacts();
            Log.I("auto", $"stuck -> reversing at ({p.x:F0},{p.y:F0}), road piece {follower.road.fid} s {follower.s:F0}/{follower.Length:F0}, wanted {want:F0} km/h"); }
        Status = $"'{follower.road.name}' {want:F0} km/h (limit {RoadFollower.LimitKmh(follower.road):F0}) lane {follower.lane:F1} m steer {car.Steer:F2} | off {off:F1} m, ang {ang:F0}, s {follower.s:F0}/{follower.Length:F0} fwd {follower.fwd} hw {follower.road.hw:F1} ow {follower.road.oneway}";
    }

    /// <summary>The car is stuck in a piece of road that leads nowhere: put it on a well-connected road a few hundred metres away, in its lane.</summary>
    void Relocate(Vector2 from)
    {
        var r = follower.RandomConnectedPiece(from, 150f, 900f, 60f);
        if (r == null) { reverseT = 2f; return; }
        Relocations++;
        float s = r.Length * 0.5f; Vector3 pt = r.At(s, out Vector2 d);
        var f = new RoadFollower(data, rng); f.Place(r, s, r.oneway != 2); follower = f;
        Vector3 lp = f.Ahead(0.1f, out Vector2 ld);
        Log.I("auto", $"cut-off road at ({from.x:F0},{from.y:F0}): relocating to ({lp.x:F0},{lp.z:F0})");
        car.Respawn(new Vector3(lp.x, lp.y + 0.9f, lp.z), Mathf.Atan2(ld.x, ld.y) * Mathf.Rad2Deg);
    }

    /// <summary>What is the car touching? (diagnostic for "stuck" events)</summary>
    void DumpContacts()
    {
        var c = car.transform.position;
        foreach (var col in Physics.OverlapSphere(c + Vector3.up * 0.6f, 3.2f))
        {
            if (col.transform == car.transform) continue;
            var mc = col as MeshCollider;
            Log.I("auto", $"   touching '{col.transform.parent?.name}/{col.name}' {col.GetType().Name} bounds {col.bounds.min:F1} .. {col.bounds.max:F1}{(mc != null ? " verts " + (mc.sharedMesh != null ? mc.sharedMesh.vertexCount : 0) : "")}");
        }
        if (follower != null)
        {
            Vector3 lp = follower.Ahead(0.1f, out Vector2 ld);
            Log.I("auto", $"   lane point ({lp.x:F1},{lp.z:F1}) heading {Mathf.Atan2(ld.x, ld.y) * Mathf.Rad2Deg:F0}, car is {Vector2.Distance(new Vector2(c.x, c.z), new Vector2(lp.x, lp.z)):F1} m from it; road hw {follower.road.hw:F1} fwd {follower.fwd}; next {(follower.nextRoad != null ? follower.nextRoad.fid : 0)} planned {follower.planned}");
        }
        for (int k = 0; k < 12; k++)
        {   // where is the nearest solid surface, in 30-degree steps around the car?
            Vector3 dir = Quaternion.Euler(0, k * 30f, 0) * Vector3.forward;
            if (Physics.Raycast(c + Vector3.up * 0.6f, dir, out RaycastHit hit, 3.5f) && hit.collider.transform != car.transform)
                Log.I("auto", $"   ray {k * 30}° hits '{hit.collider.name}' at {hit.distance:F2} m -> ({hit.point.x:F1},{hit.point.z:F1})");
        }
        Log.I("auto", $"   car at ({c.x:F1},{c.y:F1},{c.z:F1}) yaw {car.transform.eulerAngles.y:F0}, wheels on ground {car.WheelsOnGround}/4, speed {car.SpeedKmh:F1}");
    }

    void DrivePolyline(Vector2 p, Vector2 fwd, float speed, float dt)
    {
        if (path.Count < 3) { Reset(); return; }
        int ci = 0; float best = 1e9f;
        for (int i = 0; i < Mathf.Min(path.Count - 1, 40); i++) { float d = DistToSeg(p, path[i], path[i + 1]); if (d < best) { best = d; ci = i; } }
        if (ci > 0) path.RemoveRange(0, ci);
        if (best > 25f) { Status = "off-path, re-acquiring"; Reset(); return; }
        float look = 7f + speed * 0.22f;
        Vector2 target = path[path.Count - 1]; float acc = 0;
        for (int i = 0; i + 1 < path.Count; i++)
        {
            float seg = (path[i + 1] - path[i]).magnitude;
            if (acc + seg >= look) { target = Vector2.Lerp(path[i], path[i + 1], (look - acc) / Mathf.Max(seg, 1e-3f)); break; }
            acc += seg;
        }
        float ang = Vector2.SignedAngle(target - p, fwd);
        car.Steer = Mathf.Clamp(ang / 28f, -1f, 1f);
        float curve = 0; Vector2 d0 = (path[1] - path[0]).normalized; float dist = 0;
        for (int i = 1; i + 1 < path.Count && dist < 45f; i++) { dist += (path[i] - path[i - 1]).magnitude; curve = Mathf.Max(curve, Vector2.Angle(d0, (path[i + 1] - path[i]).normalized)); }
        float want = Mathf.Lerp(TargetKmh, 18f, Mathf.Clamp01(curve / 70f));
        car.Throttle = Mathf.Clamp01((want - speed) / 10f);
        car.Brake = speed > want + 8f ? Mathf.Clamp01((speed - want - 8f) / 15f) : 0f;
        car.Handbrake = false;
        if (reverseT > 0) { reverseT -= dt; car.Throttle = 0; car.Brake = 1; car.Steer = -car.Steer; if (reverseT <= 0) Reset(); return; }
        stuckT = speed < 2f && want > 15f ? stuckT + dt : 0f;
        if (stuckT > 3f) { stuckT = 0; reverseT = 1.6f; Log.I("auto", "stuck -> reversing"); }
        Status = $"polyline target {want:F0} km/h curve {curve:F0}° steer {car.Steer:F2}";
    }
}
