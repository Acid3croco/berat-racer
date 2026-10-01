using UnityEngine;

/// <summary>What a driver needs to know about the road it is on, whichever follower describes it.</summary>
public struct RoadAttr { public float hw; public int limit, kind, imp; public bool dirt, oneWay; }

/// <summary>
/// One driver's view of the road network, shared by the traffic and the player's autopilot: where it is, the path ahead, how fast
/// the road allows it to go, and what to do at the next junction. It knows nothing about vehicles: the caller moves its car
/// (kinematically or with real steering) and asks where to aim.
/// Two kinds: LaneFollower drives the lane graph built offline (BM07 worlds); RoadFollower rediscovers the network from the road
/// pieces at run time (older worlds, or -roadfollower to compare).
/// </summary>
public abstract class Follower
{
    public float lane;                                        // lateral offset to the right of the path (m)

    /// <summary>Lane graph when the loaded world has one (and -roadfollower is not given), else road pieces.</summary>
    public static Follower Create(WorldData data, System.Random rng, System.Func<int, object, bool> busy = null, object self = null)
    {
        if (UseLanes(data)) return new LaneFollower(data, rng, busy, self);
        return new RoadFollower(data, rng);
    }
    public static bool UseLanes(WorldData data) => data.Lanes.Count > 0 && System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-roadfollower") < 0;

    public abstract RoadAttr Attr { get; }
    public abstract float LimitKmh();
    /// <summary>Speed (m/s) the bends ahead allow, braking in time.</summary>
    public abstract float CurveSpeed();
    /// <summary>Speed cap (m/s) for the coming junction: its control, who has to be let through; 99 when free.</summary>
    public abstract float JunctionSpeed(float cruise);
    /// <summary>Point on the path `along` metres ahead (lateral offset included) and the direction of travel there.</summary>
    public abstract Vector3 Ahead(float along, out Vector2 dir);
    /// <summary>Where a car following exactly sits now (on the road surface) and looks (a point 4 m ahead).</summary>
    public abstract Vector3 Pose(out Vector2 dir, out Vector3 ahead);
    public abstract void Move(float ds);
    /// <summary>Follow a car that steers itself: catch up with its position. Returns its distance from the path.</summary>
    public abstract float Track(Vector2 pos);
    public abstract void Update(float dt);
    public abstract bool DeadEnd { get; }
    public abstract float ToEnd { get; }
    public abstract void Flip();
    public abstract void EnsurePlanned();
    public abstract bool SnapTo(Vector2 pos, Vector2 heading);
    /// <summary>Somewhere well connected 150 - 900 m from `from`: the follower is placed there. False if nothing is found.</summary>
    public abstract bool Relocate(Vector2 from, out Vector3 pos, out Vector2 dir);
    public abstract string Describe();
}
