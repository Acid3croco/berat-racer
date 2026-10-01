using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// The lane graph of the loaded chunks (BM07, built offline by tools/roads/lanegraph.py): every element by id, and a 25 m grid of
/// their points for "which lane is near here". An element crossing chunks is stored in each; any copy will do, they are the same.
/// </summary>
public class LaneGraph
{
    const float Cell = 25f;
    readonly Dictionary<int, LaneElem> byId = new Dictionary<int, LaneElem>();
    readonly Dictionary<long, List<LaneElem>> grid = new Dictionary<long, List<LaneElem>>();
    public readonly List<LaneElem> Lanes = new List<LaneElem>();          // the plain lanes (spawn points)

    public LaneGraph(IEnumerable<ChunkData> chunks)
    {
        foreach (var c in chunks)
            foreach (var e in c.Lanes)
            {
                if (byId.ContainsKey(e.id)) continue;
                byId.Add(e.id, e);
                if (e.kind == LaneElem.Lane) Lanes.Add(e);
                var seen = new HashSet<long>();
                foreach (var p in e.pts) { long k = Key(p.x, p.z); if (seen.Add(k)) { if (!grid.TryGetValue(k, out var l)) grid[k] = l = new List<LaneElem>(); l.Add(e); } }
            }
    }

    public int Count => byId.Count;
    public bool Has(int id) => byId.ContainsKey(id);
    public LaneElem Get(int id) => byId.TryGetValue(id, out var e) ? e : null;
    static long Key(float x, float z) => ((long)Mathf.FloorToInt(x / Cell) << 32) ^ (uint)Mathf.FloorToInt(z / Cell);

    /// <summary>The lane (not a connector) nearest to `pos` whose direction of travel is closest to `heading`, within `radius`.</summary>
    public LaneElem Nearest(Vector2 pos, Vector2 heading, float radius, out float s)
    {
        LaneElem best = null; float bestScore = 1e9f; s = 0f;
        int r = Mathf.CeilToInt(radius / Cell);
        int cx = Mathf.FloorToInt(pos.x / Cell), cz = Mathf.FloorToInt(pos.y / Cell);
        var seen = new HashSet<int>();
        for (int dx = -r; dx <= r; dx++)
            for (int dz = -r; dz <= r; dz++)
            {
                if (!grid.TryGetValue(((long)(cx + dx) << 32) ^ (uint)(cz + dz), out var list)) continue;
                foreach (var e in list)
                {
                    if (e.kind != LaneElem.Lane || !seen.Add(e.id)) continue;
                    float at = e.Project(pos, out float d); if (d > radius) continue;
                    e.At(at, out Vector2 dir);
                    float score = d + (1f - Vector2.Dot(dir, heading)) * 6f + (e.dirt ? 4f : 0f);       // the lane of my direction, paved preferred
                    if (score < bestScore) { bestScore = score; best = e; s = at; }
                }
            }
        return best;
    }
}
