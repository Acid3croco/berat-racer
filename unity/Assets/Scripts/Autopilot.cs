using System.Collections.Generic;
using UnityEngine;

/// <summary>Follows the real road network (pure pursuit). Used by the smoke test and as a demo mode (P).</summary>
public class Autopilot
{
    readonly WorldData data; readonly CarController car;
    readonly List<Vector2> path = new List<Vector2>();
    public float TargetKmh = 55f;
    float stuckT, reverseT; RoadData last; bool custom;
    public string Status = "";

    public Autopilot(WorldData d, CarController c) { data = d; car = c; Reset(); }

    static Vector2[] Pts(RoadData r) => (Vector2[])r.XZ.Clone();

    public void Reset()
    {
        path.Clear(); custom = false;
        Vector2 p = new Vector2(car.transform.position.x, car.transform.position.z);
        Vector2 fwd = new Vector2(car.transform.forward.x, car.transform.forward.z).normalized;
        float best = 1e9f; RoadData bestRoad = null; bool bestRev = false;
        foreach (var r in data.Roads)
        {
            if (r.dirt || r.bridge) continue;
            var pts = r.XZ;
            if (p.x < r.Min.x - best || p.x > r.Max.x + best || p.y < r.Min.y - best || p.y > r.Max.y + best) continue;      // bounding box cannot beat the best so far
            for (int i = 0; i + 1 < pts.Length; i++)
            {
                float d = DistToSeg(p, pts[i], pts[i + 1]);
                if (d < best) { Vector2 dir = (pts[i + 1] - pts[i]).normalized; best = d; bestRoad = r; bestRev = Vector2.Dot(dir, fwd) < 0; }
            }
        }
        if (bestRoad != null) AppendRoad(bestRoad, bestRev);
    }

    /// <summary>Follow exactly this polyline (used by tests that must cross a specific structure).</summary>
    public void FollowPolyline(IList<Vector2> pts) { path.Clear(); path.AddRange(pts); custom = true; }

    void AppendRoad(RoadData r, bool reversed)
    {
        var pts = Pts(r);
        if (reversed) System.Array.Reverse(pts);
        path.AddRange(pts);
        last = r;
    }

    static float DistToSeg(Vector2 p, Vector2 a, Vector2 b)
    {
        Vector2 ab = b - a; float t = Mathf.Clamp01(Vector2.Dot(p - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
        return (p - (a + ab * t)).magnitude;
    }

    void ExtendIfShort()
    {
        float len = 0; for (int i = 1; i < path.Count; i++) len += (path[i] - path[i - 1]).magnitude;
        int guard = 0;
        while (len < 200f && path.Count > 1 && guard++ < 6)
        {
            Vector2 end = path[path.Count - 1], dir = (end - path[path.Count - 2]).normalized;
            float bestScore = 1e9f; RoadData bestRoad = null; bool bestRev = false;
            foreach (var r in data.Roads)
            {
                if (r.dirt || r.bridge || r == last) continue;
                var pts = r.XZ;
                if (end.x < r.Min.x - 4f || end.x > r.Max.x + 4f || end.y < r.Min.y - 4f || end.y > r.Max.y + 4f) continue;
                for (int side = 0; side < 2; side++)
                {
                    Vector2 s = side == 0 ? pts[0] : pts[pts.Length - 1];
                    if ((s - end).magnitude > 4f) continue;
                    Vector2 d = side == 0 ? (pts[1] - pts[0]).normalized : (pts[pts.Length - 2] - pts[pts.Length - 1]).normalized;
                    float turn = Vector2.Angle(dir, d);
                    if (turn < bestScore) { bestScore = turn; bestRoad = r; bestRev = side == 1; }
                }
            }
            if (bestRoad == null) break;
            var np = Pts(bestRoad); if (bestRev) System.Array.Reverse(np);
            for (int i = 1; i < np.Length; i++) { path.Add(np[i]); len += (np[i] - np[i - 1]).magnitude; }
            last = bestRoad;
        }
    }

    public void Drive(float dt)
    {
        Vector2 p = new Vector2(car.transform.position.x, car.transform.position.z);
        Vector2 fwd = new Vector2(car.transform.forward.x, car.transform.forward.z).normalized;
        if (path.Count < 3) { Reset(); return; }
        // drop passed points
        int ci = 0; float best = 1e9f;
        for (int i = 0; i < Mathf.Min(path.Count - 1, 40); i++) { float d = DistToSeg(p, path[i], path[i + 1]); if (d < best) { best = d; ci = i; } }
        if (ci > 0) path.RemoveRange(0, ci);
        if (best > 25f) { Status = "off-road, re-acquiring"; Reset(); return; }
        if (!custom) ExtendIfShort();

        float speed = car.SpeedKmh, look = 7f + speed * 0.22f;
        Vector2 target = path[path.Count - 1]; float acc = 0;
        for (int i = 0; i + 1 < path.Count; i++)
        {
            float seg = (path[i + 1] - path[i]).magnitude;
            if (acc + seg >= look) { target = Vector2.Lerp(path[i], path[i + 1], (look - acc) / Mathf.Max(seg, 1e-3f)); break; }
            acc += seg;
        }
        float ang = Vector2.SignedAngle(target - p, fwd);          // + = target is to the right of forward
        car.Steer = Mathf.Clamp(ang / 28f, -1f, 1f);

        // slow down for upcoming bends
        float curve = 0; Vector2 d0 = (path[1] - path[0]).normalized; float dist = 0;
        for (int i = 1; i + 1 < path.Count && dist < 45f; i++) { dist += (path[i] - path[i - 1]).magnitude; curve = Mathf.Max(curve, Vector2.Angle(d0, (path[i + 1] - path[i]).normalized)); }
        float want = Mathf.Lerp(TargetKmh, 18f, Mathf.Clamp01(curve / 70f));
        car.Throttle = Mathf.Clamp01((want - speed) / 10f);
        car.Brake = speed > want + 8f ? Mathf.Clamp01((speed - want - 8f) / 15f) : 0f;
        car.Handbrake = false;

        // stuck? reverse a bit, then re-acquire
        if (reverseT > 0) { reverseT -= dt; car.Throttle = 0; car.Brake = 1; car.Steer = -car.Steer; if (reverseT <= 0) Reset(); return; }
        stuckT = speed < 2f && want > 15f ? stuckT + dt : 0f;
        if (stuckT > 3f) { stuckT = 0; reverseT = 1.6f; Log.I("auto", "stuck -> reversing"); }
        Status = $"following '{(last != null ? last.name : "-")}' target {want:F0} km/h curve {curve:F0}° steer {car.Steer:F2}";
    }
}
