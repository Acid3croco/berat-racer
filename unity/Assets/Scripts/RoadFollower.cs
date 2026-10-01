using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// One driver's view of the road network, shared by the traffic AI and the player's autopilot: which road piece it is on, how far along, which lane it holds
/// (right-hand traffic), where it goes at the next junction (uniformly random exit, one-way roads honoured), how fast the road allows it to go (legal limit, bends).
/// It knows nothing about vehicles: the caller moves its car (kinematically or with real steering) and asks where to aim.
/// </summary>
public class RoadFollower
{
    public const float LaneMax = 2.1f;
    public RoadData road; public bool fwd; public float s, lane;
    public RoadData nextRoad; public bool nextFwd, nextSharp, planned;
    readonly WorldData data; readonly System.Random rng;

    public RoadFollower(WorldData d, System.Random r) { data = d; rng = r; }

    public float Length => road.Length;
    public float ToEnd => fwd ? road.Length - s : s;

    // ------------------------------------------------------------------ speed limits
    /// <summary>Speed allowed on this road (km/h) in the direction of travel: the French legal limit from the data, no more than 1.3 x the road's average speed + 8 (winding, narrow roads are slower).</summary>
    public static float LimitKmh(RoadData r, bool forward = true)
    {
        float limit = !forward && r.limitBack > 0 ? r.limitBack : r.limit;
        if (limit <= 0f) limit = r.imp == "1" ? 110f : r.imp == "2" ? 90f : r.imp == "3" || r.imp == "4" ? 80f : 70f;        // data without limits: guess from the road class
        if (r.avg > 0) limit = Mathf.Min(limit, Mathf.Max(r.avg * 1.3f + 8f, 30f));
        return limit;
    }

    public static float LaneOffset(RoadData r) => r.oneway == 0 ? Mathf.Clamp(r.hw * 0.5f, 0f, Mathf.Min(LaneMax, Mathf.Max(0f, r.hw - 1.35f))) : 0f;         // right-hand traffic: half a lane right of the centre line, but a car (1.8 m wide) on a narrow road stays inside it

    // ------------------------------------------------------------------ placing and moving
    public void Place(RoadData r, float along, bool forward) { road = r; s = along; fwd = forward; lane = LaneOffset(r); planned = false; nextRoad = null; nextSharp = false; }

    /// <summary>Nearest road piece to a world position (paved roads preferred), travelling in the direction closest to `heading`. False if nothing within 40 m.</summary>
    public bool SnapTo(Vector2 pos, Vector2 heading)
    {
        RoadData best = null; float bestD = 40f, bestS = 0f;
        foreach (var r in data.Roads)
        {
            var xz = r.XZ; if (xz.Length < 2) continue;
            if (pos.x < r.Min.x - bestD || pos.x > r.Max.x + bestD || pos.y < r.Min.y - bestD || pos.y > r.Max.y + bestD) continue;
            float acc = 0f;
            for (int i = 0; i + 1 < xz.Length; i++)
            {
                Vector2 a = xz[i], b = xz[i + 1], ab = b - a; float len = ab.magnitude, t = Mathf.Clamp01(Vector2.Dot(pos - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                float d = (pos - (a + ab * t)).magnitude + (r.dirt ? 4f : 0f);
                if (d < bestD) { bestD = d; best = r; bestS = acc + t * len; }
                acc += len;
            }
        }
        if (best == null) return false;
        best.At(bestS, out Vector2 dInc);
        bool f = Vector2.Dot(dInc, heading) >= 0f;
        if (best.oneway == 1) f = true; else if (best.oneway == 2) f = false;
        Place(best, bestS, f);
        return true;
    }

    /// <summary>Follow a car that steers itself: move `s` to the point of this piece nearest to `pos` (searching around the old value); crossing to the next piece at the end. Returns the distance to the road centre line.</summary>
    public float Track(Vector2 pos)
    {
        EnsurePlanned();
        float bestS = s, bestD = 1e9f;
        float lo = fwd ? s - 8f : s - 35f, hi = fwd ? s + 35f : s + 8f;                              // search mostly ahead of the car, a little behind
        for (float q = lo; q <= hi; q += 1f)
        {
            float qq = Mathf.Clamp(q, 0f, road.Length); Vector3 p = road.At(qq, out _);
            float d = (new Vector2(p.x, p.z) - pos).sqrMagnitude; if (d < bestD) { bestD = d; bestS = qq; }
        }
        s = bestS;
        if (ToEnd < 2.5f) Move(3f);
        return Mathf.Sqrt(bestD);
    }

    /// <summary>Advance `ds` metres along the travel direction, changing piece at the end.</summary>
    public void Move(float ds)
    {
        s += (fwd ? 1f : -1f) * ds;
        float len = road.Length;
        if (fwd ? s >= len : s <= 0f) Advance(fwd ? s - len : -s);
    }

    void Advance(float overflow)
    {
        if (!planned) Plan();
        if (nextRoad != null)
        {
            road = nextRoad; fwd = nextFwd;
            s = fwd ? Mathf.Min(overflow, road.Length) : Mathf.Max(road.Length - overflow, 0f);
        }
        else
        {   // dead end: turn round in the opposite lane
            fwd = !fwd; s = fwd ? Mathf.Min(overflow, road.Length) : Mathf.Max(road.Length - overflow, 0f);
        }
        planned = false; nextRoad = null; nextSharp = false;
    }

    public bool DeadEnd => planned && nextRoad == null;
    /// <summary>Turn round on the spot: travel the same piece the other way.</summary>
    public void Flip() { fwd = !fwd; planned = false; nextRoad = null; nextSharp = false; }

    /// <summary>A road piece from which one can get somewhere: it has an exit at one end at least, in the direction of travel or against it.</summary>
    public bool Connected(RoadData r) { return Options(r, true).Count + Options(r, false).Count > 0; }

    /// <summary>Random paved piece of the loaded map that leads somewhere, at least `minLength` long.</summary>
    public RoadData RandomConnectedPiece(Vector2 near, float minDist, float maxDist, float minLength)
    {
        var roads = data.Roads;
        for (int i = 0; i < 60; i++)
        {
            var r = roads[rng.Next(roads.Length)];
            if (r.dirt || r.bridge || r.hw < 1.9f || r.Length < minLength) continue;
            Vector3 m = r.At(r.Length * 0.5f, out _); float d = Vector2.Distance(near, new Vector2(m.x, m.z));
            if (d < minDist || d > maxDist) continue;
            if (Connected(r)) return r;
        }
        return null;
    }

    public void EnsurePlanned() { if (!planned && ToEnd < 90f) Plan(); }

    // ------------------------------------------------------------------ geometry
    // ------------------------------------------------------------------ the path ahead
    // The lane path for the next ~75 m, sampled every 1.5 m through the planned pieces, with corners (junction turns) smoothed over ~10 m so a car can actually drive it.
    readonly List<Vector3> pathPts = new List<Vector3>(); readonly List<float> pathCum = new List<float>();
    RoadData pathRoad; bool pathFwd; float pathS = -999f; RoadData pathNext; float pathLane = -999f;
    const float PathStep = 1.5f, PathLength = 75f;

    Vector3 LanePoint(RoadData r, float pos, bool f, float laneOffset)
    {
        Vector3 p = r.At(pos, out Vector2 dInc); Vector2 d = f ? dInc : -dInc; Vector2 right = new Vector2(d.y, -d.x);
        return new Vector3(p.x + right.x * laneOffset, p.y, p.z + right.y * laneOffset);
    }

    void EnsurePath()
    {
        EnsurePlanned();
        if (pathRoad == road && pathFwd == fwd && pathNext == nextRoad && Mathf.Abs(s - pathS) < 2.5f && Mathf.Abs(lane - pathLane) < 0.3f && pathPts.Count > 2) return;
        pathRoad = road; pathFwd = fwd; pathNext = nextRoad; pathS = s; pathLane = lane;
        pathPts.Clear(); pathCum.Clear();
        RoadData r = road; bool f = fwd; float pos = s, travelled = 0f; int pieces = 0;
        pathPts.Add(LanePoint(r, pos, f, lane));
        while (travelled < PathLength && pieces < 6)
        {
            float toEnd = f ? r.Length - pos : pos;
            if (toEnd <= PathStep)
            {
                RoadData nr; bool nf;
                if (r == road && planned) { nr = nextRoad; nf = nextFwd; }
                else { var o = Options(r, f); if (o.Count == 0) { nr = null; nf = f; } else { var pick = o[0]; foreach (var c in o) if (c.turn < pick.turn) pick = c; nr = pick.r; nf = pick.fwd; } }
                pathPts.Add(LanePoint(r, f ? r.Length : 0f, f, r == road ? lane : LaneOffset(r)));
                if (nr == null) break;
                r = nr; f = nf; pos = f ? 0f : r.Length; pieces++;
                pathPts.Add(LanePoint(r, pos, f, LaneOffset(r))); travelled += PathStep;
                continue;
            }
            pos += (f ? 1f : -1f) * PathStep; travelled += PathStep;
            pathPts.Add(LanePoint(r, pos, f, r == road ? lane : LaneOffset(r)));
        }
        // smooth: two passes of a +-3 sample moving average (the first point stays put), which rounds a 90-degree junction turn into a ~6 m radius curve
        for (int pass = 0; pass < 2; pass++)
        {
            var src = pathPts.ToArray();
            for (int i = 1; i < src.Length; i++)
            {
                Vector3 sum = Vector3.zero; int n = 0;
                for (int k = -3; k <= 3; k++) { int j = Mathf.Clamp(i + k, 0, src.Length - 1); sum += src[j]; n++; }
                pathPts[i] = sum / n; pathPts[i] = new Vector3(pathPts[i].x, src[i].y, pathPts[i].z);
            }
        }
        float acc = 0f; pathCum.Add(0f);
        for (int i = 1; i < pathPts.Count; i++) { acc += Vector2.Distance(new Vector2(pathPts[i].x, pathPts[i].z), new Vector2(pathPts[i - 1].x, pathPts[i - 1].z)); pathCum.Add(acc); }
    }

    /// <summary>Position on the lane path `along` metres ahead of the current point (crossing into the planned next pieces, corners smoothed), and the travel direction there.</summary>
    public Vector3 Ahead(float along, out Vector2 dirTravel)
    {
        EnsurePath();
        int n = pathPts.Count; float total = pathCum[n - 1];
        // the path was sampled from pathS; the follower may have moved a little since then
        float shift = fwd ? s - pathS : pathS - s;
        float d = Mathf.Clamp(along + Mathf.Max(0f, shift), 0f, total);
        int i = pathCum.BinarySearch(d); if (i < 0) i = ~i; i = Mathf.Clamp(i, 1, n - 1);
        float seg = Mathf.Max(pathCum[i] - pathCum[i - 1], 1e-4f), t = (d - pathCum[i - 1]) / seg;
        Vector3 p = Vector3.Lerp(pathPts[i - 1], pathPts[i], t);
        Vector2 dv = new Vector2(pathPts[i].x - pathPts[i - 1].x, pathPts[i].z - pathPts[i - 1].z);
        dirTravel = dv.sqrMagnitude > 1e-8f ? dv.normalized : Vector2.up;
        return p;
    }

    /// <summary>Speed (m/s) that keeps lateral acceleration at ~2.4 m/s2 through the tightest bend in the next 60 m.</summary>
    public float CurveSpeed()
    {
        float sgn = fwd ? 1f : -1f, worst = 0f; road.At(s, out Vector2 d0); d0 = fwd ? d0 : -d0; Vector2 prev = d0;
        for (int k = 1; k <= 3; k++)
        {
            road.At(s + sgn * 20f * k, out Vector2 dk); dk = fwd ? dk : -dk;
            float ang = Vector2.Angle(prev, dk) * Mathf.Deg2Rad; prev = dk;
            worst = Mathf.Max(worst, ang / 20f);
        }
        return worst < 1e-4f ? 99f : Mathf.Max(5f, Mathf.Sqrt(2.4f / worst));
    }

    /// <summary>Speed cap for the coming junction or turn (m/s), 99 when the way ahead is straight and free.</summary>
    public float JunctionSpeed(float cruise)
    {
        EnsurePlanned();
        if (!planned || !nextSharp || ToEnd >= 45f) return 99f;
        return Mathf.Lerp(6.5f, cruise, Mathf.Clamp01((ToEnd - 8f) / 37f));
    }

    // ------------------------------------------------------------------ routing
    public struct Option { public RoadData r; public bool fwd; public float turn; }

    /// <summary>Choose the next road piece at the end of this one: every possible exit is equally likely, one-way roads are honoured.</summary>
    void Plan()
    {
        planned = true; nextRoad = null;
        var opts = Options(road, fwd);
        if (opts.Count == 0) { nextSharp = false; return; }
        var chosen = opts[rng.Next(opts.Count)];
        nextRoad = chosen.r; nextFwd = chosen.fwd; nextSharp = opts.Count > 1 || chosen.turn > 25f;
    }

    /// <summary>Exits at the end of a piece: paved roads first; tracks only when there is nothing else (so a drive never dead-ends at a track junction).</summary>
    List<Option> Options(RoadData from, bool forward)
    {
        var paved = Options(from, forward, false);
        return paved.Count > 0 ? paved : Options(from, forward, true);
    }

    List<Option> Options(RoadData from, bool forward, bool allowDirt)
    {
        var res = new List<Option>();
        Vector3 e = forward ? from.EndPoint : from.StartPoint;
        from.At(forward ? from.Length : 0f, out Vector2 dInc);
        Vector2 dirOut = forward ? dInc : -dInc;                                                    // direction of travel at the end of this piece
        foreach (var r in data.Roads)
        {
            if (r == from || (r.dirt && !allowDirt) || r.hw < (allowDirt ? 1.0f : 1.6f) || r.Length < 0.3f) continue;
            for (int end = 0; end < 2; end++)
            {
                bool enterFwd = end == 0;                                                          // entering at the start means travelling in the increasing direction
                if ((enterFwd && r.oneway == 2) || (!enterFwd && r.oneway == 1)) continue;
                Vector3 q = enterFwd ? r.StartPoint : r.EndPoint;
                if ((q.x - e.x) * (q.x - e.x) + (q.z - e.z) * (q.z - e.z) > 2.5f * 2.5f) continue;
                r.At(enterFwd ? 0.5f : Mathf.Max(0f, r.Length - 0.5f), out Vector2 dIn);
                Vector2 dirIn = enterFwd ? dIn : -dIn;
                float turn = Vector2.Angle(dirOut, dirIn);
                if (turn > 115f) continue;
                res.Add(new Option { r = r, fwd = enterFwd, turn = turn });
            }
        }
        return res;
    }
}
