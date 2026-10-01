using UnityEngine;

/// <summary>
/// The embankment ribbons of BM07 worlds (tools/roads/terrain.py): along every road edge and junction kerb, a shoulder just under the
/// road, a slope (cut or fill) to the LiDAR ground, then the ground out to at least 6.5 m from the edge, then a 6 m apron that redraws
/// the terrain as it was before the builder lowered it under the ribbon (a lowered 4 m triangle reaches that far). They are the ground
/// the player sees and drives on beside the road. Each cross-section is seven points: edge, end of the gravel strip, end of the
/// shoulder, toe of the slope, outer end, apron middle, apron end; quads are split on the same diagonals as the builder's triangles.
/// </summary>
public static class Ribbons
{
    public const float VergeDrop = 0.04f, Gravel = 0.45f, Lip = 0.5f;
    public const int Points = 7, Profile = 8;

    /// <summary>Cross-section beside `edge`, outward `dir` (unit, x / z), profile p[o .. o + 7]: shoulder, toe distance, toe height, outer distance, outer height, apron heights, apron width.</summary>
    public static void Section(Vector3 edge, Vector2 dir, float[] p, int o, Vector3[] pts)
    {
        float shoulder = Mathf.Max(p[o], 0.05f);
        Vector3 at(float d, float y) => new Vector3(edge.x + dir.x * d, y, edge.z + dir.y * d);
        Vector3 s = at(shoulder, edge.y - VergeDrop);
        pts[0] = edge;
        pts[1] = Vector3.Lerp(edge, s, Mathf.Min(Gravel / shoulder, 1f));
        pts[2] = s;
        pts[3] = at(p[o + 1], p[o + 2]);
        pts[4] = at(p[o + 3], p[o + 4]);
        pts[5] = at(p[o + 3] + p[o + 7] * 0.5f, p[o + 5]);
        pts[6] = at(p[o + 3] + p[o + 7], p[o + 6]);
    }

    static Vector2 Out(RoadData r, int i, bool left)
    {
        Vector2 d = new Vector2(r.left[i * 3] - r.right[i * 3], r.left[i * 3 + 2] - r.right[i * 3 + 2]);
        d = d.sqrMagnitude > 1e-8f ? d.normalized : Vector2.right;
        return left ? d : -d;
    }

    public static bool Has(RoadData r) => r.ribbon != null && !r.bridge && !r.tunnel;

    /// <summary>Cross-section of a road's ribbon at point i, on its left (side 0) or right (side 1).</summary>
    public static void RoadSection(RoadData r, int i, int side, Vector3[] pts) =>
        Section(r.P(side == 0 ? r.left : r.right, i), Out(r, i, side == 0), r.ribbon, (i * 2 + side) * Profile, pts);

    /// <summary>Cross-section of a junction kerb's ribbon at vertex i.</summary>
    public static void JunctionSection(JunctionData j, int i, Vector3[] pts)
    {
        const int w = Profile + 2;
        Section(new Vector3(j.v[i * 3], j.v[i * 3 + 1], j.v[i * 3 + 2]), new Vector2(j.ribbon[i * w], j.ribbon[i * w + 1]), j.ribbon, i * w + 2, pts);
    }

    /// <summary>Is a triangle wound clockwise seen from above? A ribbon triangle that is not has folded over (a sharp bend): it is left out.</summary>
    public static bool Upright(Vector3 a, Vector3 b, Vector3 c) => (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x) < -1e-6f;

    /// <summary>The two triangles between sections a (earlier) and b (later), from point q to q + 1, wound clockwise seen from above
    /// (Unity's front face) and split on the a[q] - b[q + 1] diagonal; `left`: the ribbon lies left of the direction a -> b.</summary>
    public static void Quad<T>(T ia, T ib, T ob, T oa, bool left, System.Action<T, T, T> tri)
    {
        if (left) { tri(ia, oa, ob); tri(ia, ob, ib); }
        else { tri(ia, ib, ob); tri(ia, ob, oa); }
    }

    /// <summary>Every ribbon triangle of these roads and junctions, for the physics (the gravel point is left out: it lies on the shoulder's plane).</summary>
    public static void Triangles(RoadData[] roads, JunctionData[] junctions, System.Action<Vector3, Vector3, Vector3> tri)
    {
        var a = new Vector3[Points]; var b = new Vector3[Points]; int[] keep = { 0, 2, 3, 4, 5, 6 };
        void Between(bool left)
        {
            for (int q = 0; q + 1 < keep.Length; q++) Quad(a[keep[q]], b[keep[q]], b[keep[q + 1]], a[keep[q + 1]], left, (x, y, z) => { if (Upright(x, y, z)) tri(x, y, z); });
        }
        foreach (var r in roads)
        {
            if (!Has(r)) continue;
            for (int k = 0; k + 1 < r.Count; k++)
            {
                if (!r.drawn[k]) continue;
                for (int side = 0; side < 2; side++) { RoadSection(r, k, side, a); RoadSection(r, k + 1, side, b); Between(side == 0); }
            }
        }
        foreach (var j in junctions)
        {
            if (j.ribbon == null) continue;
            for (int e = 0; e < j.mouth.Length; e++)
            {
                if (j.mouth[e]) continue;
                JunctionSection(j, j.edge[e * 2], a); JunctionSection(j, j.edge[e * 2 + 1], b); Between(false);
            }
        }
    }
}
