using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Follows the lane graph built offline (BM07): lanes along the roads at their real lane centres, connectors through junctions that
/// are already smooth curves. The route ahead is a list of element ids, extended at random among the successors (paved first);
/// nothing is searched at run time. Speed comes from the curvature speeds precomputed per point, and at a connector from its
/// control (stop, give way, priority to the right, lights) and the connectors it has to let through (`busy` says which are taken).
/// </summary>
public class LaneFollower : Follower
{
    const float Brake = 2.5f, PlanAhead = 160f, LateralRate = 1.1f;
    readonly WorldData data; readonly System.Random rng; readonly System.Func<int, object, bool> busy;
    public object Self;                                                        // who `busy` should not count (the car this follower drives)
    public LaneElem elem; public float s;
    readonly List<int> route = new List<int>();
    bool deadEnd; float waited; int stoppedAt = -1;

    public LaneFollower(WorldData d, System.Random r, System.Func<int, object, bool> busy, object self) { data = d; rng = r; this.busy = busy; Self = self; }

    LaneGraph Graph => data.Lanes;
    public override RoadAttr Attr => new RoadAttr { hw = elem.hw, limit = elem.limit, kind = elem.roadKind, imp = elem.imp, dirt = elem.dirt, oneWay = elem.left < 0 && elem.right < 0 };
    public override float LimitKmh() => elem.limit > 0 ? elem.limit : 50f;
    public override bool DeadEnd { get { EnsurePlanned(); return deadEnd; } }
    public override float ToEnd
    {
        get
        {
            EnsurePlanned(); float d = elem.Length - s;
            if (deadEnd) foreach (int id in route) { var e = Graph.Get(id); if (e != null) d += e.Length; }
            return d;
        }
    }
    public int NextConnector { get { EnsurePlanned(); foreach (int id in route) { var e = Graph.Get(id); if (e != null && e.kind == LaneElem.Connector) return id; } return -1; } }
    public float DistanceTo(int id)
    {
        float d = elem.Length - s;
        foreach (int r in route) { if (r == id) return d; var e = Graph.Get(r); if (e == null) break; d += e.Length; }
        return 1e9f;
    }

    /// <summary>The next element is a U-turn at a dead end and it starts within `within` metres.</summary>
    public bool UTurnAhead(float within)
    {
        EnsurePlanned();
        return route.Count > 1 && Graph.Get(route[0]) is LaneElem u && u.kind == LaneElem.UTurn && elem.Length - s < within;
    }
    /// <summary>Skip the U-turn: continue on the lane it leads to (a real car turns round on the spot there).</summary>
    public void TakeUTurn()
    {
        var u = Graph.Get(route[0]); var next = Graph.Get(route[1]);
        if (u == null || next == null) return;
        route.RemoveRange(0, 2); elem = next; s = 0f;
    }

    public void Place(LaneElem e, float along) { elem = e; s = Mathf.Clamp(along, 0f, e.Length); route.Clear(); deadEnd = false; lane = 0f; }

    // ------------------------------------------------------------------ route
    public override void EnsurePlanned()
    {
        float ahead = elem.Length - s; LaneElem last = elem;
        foreach (int id in route) { var e = Graph.Get(id); if (e == null) { deadEnd = true; return; } ahead += e.Length; last = e; }
        deadEnd = false;
        while (ahead < PlanAhead)
        {
            var next = Choose(last);
            if (next == null) { deadEnd = true; return; }
            route.Add(next.id); ahead += next.Length; last = next;
        }
    }

    /// <summary>A successor at random: paved before dirt, any connector equally likely.</summary>
    LaneElem Choose(LaneElem from)
    {
        LaneElem pick = null; int paved = 0, any = 0;
        foreach (int id in from.succ)
        {
            var e = Graph.Get(id); if (e == null) continue;
            any++;
            if (!e.dirt) { paved++; if (rng.Next(paved) == 0) pick = e; }
        }
        if (pick != null) return pick;
        int k = 0;
        foreach (int id in from.succ) { var e = Graph.Get(id); if (e != null && rng.Next(++k) == 0) pick = e; }
        return pick;
    }

    // ------------------------------------------------------------------ moving
    public override void Move(float ds)
    {
        s += ds;
        while (s > elem.Length)
        {
            EnsurePlanned();
            if (route.Count == 0 || Graph.Get(route[0]) == null) { s = elem.Length; deadEnd = true; return; }
            s -= elem.Length; elem = Graph.Get(route[0]); route.RemoveAt(0);
        }
    }

    public override float Track(Vector2 pos)
    {
        float at = elem.Project(pos, out float d);
        EnsurePlanned();
        while (at >= elem.Length - 0.3f && route.Count > 0)
        {   // past the end: the next element takes over if the car is no further from it
            var next = Graph.Get(route[0]); if (next == null) break;
            float at2 = next.Project(pos, out float d2); if (d2 > d + 0.5f) break;
            elem = next; route.RemoveAt(0); at = at2; d = d2; EnsurePlanned();
        }
        s = at;
        return d;
    }

    public override void Update(float dt) { lane = Mathf.MoveTowards(lane, 0f, LateralRate * dt); }

    /// <summary>Move to the lane beside (side -1 left, +1 right) where the car is now, keeping the car where it is (the offset eases out).</summary>
    public bool ChangeLane(int side)
    {
        var other = Graph.Get(side < 0 ? elem.left : elem.right); if (other == null) return false;
        Vector3 here = elem.At(s, out Vector2 dir); Vector2 right = new Vector2(dir.y, -dir.x);
        Vector2 p = new Vector2(here.x, here.z) + right * lane;
        float at = other.Project(p, out _); Vector3 q = other.At(at, out _);
        lane = Vector2.Dot(p - new Vector2(q.x, q.z), right);
        elem = other; s = at; route.Clear(); deadEnd = false;
        return true;
    }
    public bool CanChange(int side) => Graph.Get(side < 0 ? elem.left : elem.right) != null;
    public Vector3 PointBeside(int side, float along)
    {
        var other = Graph.Get(side < 0 ? elem.left : elem.right); if (other == null) return Vector3.zero;
        Vector3 here = elem.At(s, out _); float at = other.Project(new Vector2(here.x, here.z), out _);
        return other.At(at + along, out _);
    }

    public override void Flip()
    {
        Vector3 here = elem.At(s, out Vector2 dir);
        var other = Graph.Nearest(new Vector2(here.x, here.z), -dir, 10f, out float at);
        if (other != null && other != elem) Place(other, at);
    }

    // ------------------------------------------------------------------ the path ahead
    public override Vector3 Ahead(float along, out Vector2 dir)
    {
        EnsurePlanned();
        float pos = s + Mathf.Max(along, 0f); LaneElem e = elem; int k = 0;
        while (pos > e.Length && k < route.Count) { var n = Graph.Get(route[k]); if (n == null) break; pos -= e.Length; e = n; k++; }
        Vector3 p = e.At(pos, out dir);
        float off = lane * Mathf.Clamp01(1f - along / 15f);                      // a lane change offset fades over the next 15 m
        return new Vector3(p.x + dir.y * off, p.y, p.z - dir.x * off);
    }

    public override Vector3 Pose(out Vector2 dir, out Vector3 ahead)
    {
        Vector3 p = Ahead(0f, out dir); ahead = Ahead(4f, out _);
        return p;
    }

    public override float CurveSpeed()
    {
        EnsurePlanned();
        float best = 99f, d = 0f; LaneElem e = elem; float from = s; int k = 0;
        while (d < 90f)
        {
            int i = System.Array.BinarySearch(e.cum, from); if (i < 0) i = ~i;
            for (; i < e.pts.Length && d + e.cum[i] - from < 90f; i++)
                best = Mathf.Min(best, Mathf.Sqrt(e.speed[i] * e.speed[i] + 2f * Brake * Mathf.Max(0f, d + e.cum[i] - from)));
            if (k >= route.Count) break;
            d += e.Length - from; e = Graph.Get(route[k++]); from = 0f; if (e == null) break;
        }
        if (deadEnd) best = Mathf.Min(best, Mathf.Sqrt(2f * Brake * Mathf.Max(ToEnd - 2f, 0f)));      // nowhere to go: stop before the end
        return best;
    }

    public override float JunctionSpeed(float cruise)
    {
        int id = NextConnector; if (id < 0) return 99f;
        float d = DistanceTo(id); if (d > 50f) return 99f;
        var c = Graph.Get(id);
        bool blocked = false;
        if (busy != null) foreach (int y in c.yields) if (busy(y, Self)) { blocked = true; break; }
        float cap = 99f;
        if (c.control == LaneElem.Stop && stoppedAt != id)
        {
            cap = Mathf.Sqrt(2f * 2f * Mathf.Max(d - 1f, 0f));
            if (d < 3f) { waited += Time.fixedDeltaTime; if (waited > 1f) { stoppedAt = id; waited = 0f; } }
        }
        else if (c.control != LaneElem.Priority && c.control != LaneElem.Signals)
            cap = Mathf.Lerp(6f, cruise, Mathf.Clamp01((d - 8f) / 37f));            // look before going in
        if (blocked)
        {
            float hold = Mathf.Sqrt(2f * Brake * Mathf.Max(d - 1.5f, 0f));
            if (d < 4f) waited += Time.fixedDeltaTime;
            if (waited < 5f) cap = Mathf.Min(cap, hold);                          // waited long enough (everyone yielding to everyone): go
        }
        else if (stoppedAt != id && c.control != LaneElem.Stop) waited = 0f;
        return cap;
    }

    // ------------------------------------------------------------------ placing
    public override bool SnapTo(Vector2 pos, Vector2 heading)
    {
        var e = Graph.Nearest(pos, heading, 40f, out float at); if (e == null) return false;
        Place(e, at); return true;
    }

    public override bool Relocate(Vector2 from, out Vector3 pos, out Vector2 dir)
    {
        var lanes = Graph.Lanes; pos = Vector3.zero; dir = Vector2.up;
        for (int i = 0; i < 80 && lanes.Count > 0; i++)
        {
            var e = lanes[rng.Next(lanes.Count)];
            if (e.dirt || e.hw < 1.9f || e.Length < 60f || e.succ.Length == 0) continue;
            Vector3 m = e.At(e.Length * 0.5f, out Vector2 d0); float dist = Vector2.Distance(from, new Vector2(m.x, m.z));
            if (dist < 150f || dist > 900f) continue;
            Place(e, e.Length * 0.5f); pos = m; dir = d0; return true;
        }
        return false;
    }

    public override string Describe() => $"lane {elem.id} ({(elem.kind == LaneElem.Connector ? "junction" : "road")}) s {s:F0}/{elem.Length:F0}";
}
