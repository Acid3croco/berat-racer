using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

/// <summary>Accumulates vertex-coloured triangles into one Mesh.</summary>
public class MeshBuilder
{
    public readonly List<Vector3> V = new List<Vector3>();
    public readonly List<Color32> C = new List<Color32>();
    public readonly List<int> T = new List<int>();
    public readonly List<Vector3> U = new List<Vector3>();     // optional second channel (TEXCOORD1), zero where not set: the terrain's ground class, row direction and pattern weight

    public int Vertex(Vector3 p, Color32 c) { V.Add(p); C.Add(c); return V.Count - 1; }
    public void Tri(int a, int b, int c) { T.Add(a); T.Add(b); T.Add(c); }
    public void Quad(int a, int b, int c, int d) { Tri(a, b, c); Tri(a, c, d); }
    public bool Empty => T.Count == 0;

    /// <summary>Sets vertex i's second channel; vertices before it without one get zero.</summary>
    public void Uv(int i, Vector3 uv) { while (U.Count <= i) U.Add(Vector3.zero); U[i] = uv; }

    /// <summary>Box with optional narrower top (topScale.x/z multiply the top face).</summary>
    public void Box(Vector3 center, Vector3 size, Color32 col, Vector2? topScale = null, float topShiftZ = 0f)
    {
        var s = topScale ?? Vector2.one;
        Vector3 h = size * 0.5f;
        int[] i = new int[8];
        for (int k = 0; k < 8; k++)
        {
            bool top = (k & 4) != 0;
            float sx = (k & 1) != 0 ? 1 : -1, sz = (k & 2) != 0 ? 1 : -1;
            float fx = top ? s.x : 1, fz = top ? s.y : 1;
            var p = new Vector3(sx * h.x * fx, top ? h.y : -h.y, sz * h.z * fz + (top ? topShiftZ : 0));
            i[k] = Vertex(center + p, col);
        }
        Quad(i[0], i[1], i[3], i[2]); Quad(i[4], i[6], i[7], i[5]);   // bottom, top
        Quad(i[0], i[4], i[5], i[1]); Quad(i[2], i[3], i[7], i[6]);   // -z, +z
        Quad(i[0], i[2], i[6], i[4]); Quad(i[1], i[5], i[7], i[3]);   // -x, +x
    }

    /// <summary>Box rotated by q about its centre.</summary>
    public void BoxQ(Vector3 center, Vector3 size, Quaternion q, Color32 col)
    {
        Vector3 h = size * 0.5f; int[] i = new int[8];
        for (int k = 0; k < 8; k++)
        {
            var p = new Vector3((k & 1) != 0 ? h.x : -h.x, (k & 4) != 0 ? h.y : -h.y, (k & 2) != 0 ? h.z : -h.z);
            i[k] = Vertex(center + q * p, col);
        }
        Quad(i[0], i[1], i[3], i[2]); Quad(i[4], i[6], i[7], i[5]); Quad(i[0], i[4], i[5], i[1]); Quad(i[2], i[3], i[7], i[6]); Quad(i[0], i[2], i[6], i[4]); Quad(i[1], i[5], i[7], i[3]);
    }

    /// <summary>
    /// Extrudes a side silhouette across the car. Each point is (x = z position, y = height, z = half width at that point),
    /// listed around the outline; different half widths per point give tumblehome and tapered noses. Concave outlines (wheel arches) are fine.
    /// </summary>
    public void Prism(IList<Vector3> pts, Color32 col)
    {
        int n = pts.Count; var poly = new List<Vector2>(n); float area = 0;
        for (int k = 0; k < n; k++) { poly.Add(new Vector2(pts[k].x, pts[k].y)); var a = pts[k]; var b = pts[(k + 1) % n]; area += a.x * b.y - b.x * a.y; }
        var idxOrder = new List<int>(n); for (int k = 0; k < n; k++) idxOrder.Add(k);
        if (area < 0) { poly.Reverse(); idxOrder.Reverse(); }
        var tri = Triangulate(poly);
        int[] l = new int[n], r = new int[n];
        for (int k = 0; k < n; k++)
        {
            l[k] = Vertex(new Vector3(-pts[k].z, pts[k].y, pts[k].x), col);
            r[k] = Vertex(new Vector3(pts[k].z, pts[k].y, pts[k].x), col);
        }
        for (int k = 0; k + 2 < tri.Count; k += 3)
        {   // side caps (indices refer to the possibly reversed polygon)
            int a = idxOrder[tri[k]], b = idxOrder[tri[k + 1]], c = idxOrder[tri[k + 2]];
            Tri(l[a], l[b], l[c]); Tri(r[a], r[b], r[c]);
        }
        for (int k = 0; k < n; k++) { int m = (k + 1) % n; Quad(l[k], l[m], r[m], r[k]); }
    }

    /// <summary>Cylinder along the Z axis (round headlights, exhaust tips).</summary>
    public void CylinderZ(Vector3 center, float radius, float length, int sides, Color32 col)
    {
        int[] a = new int[sides], b = new int[sides];
        for (int k = 0; k < sides; k++)
        {
            float t = k * Mathf.PI * 2 / sides; float x = Mathf.Cos(t) * radius, y = Mathf.Sin(t) * radius;
            a[k] = Vertex(center + new Vector3(x, y, -length / 2), col); b[k] = Vertex(center + new Vector3(x, y, length / 2), col);
        }
        for (int k = 0; k < sides; k++)
        {
            int m = (k + 1) % sides; Quad(a[k], b[k], b[m], a[m]);
            if (k >= 1 && k < sides - 1) { Tri(a[0], a[k], a[k + 1]); Tri(b[0], b[k + 1], b[k]); }
        }
    }

    /// <summary>Cylinder along the X axis (for wheels).</summary>
    public void CylinderX(Vector3 center, float radius, float width, int sides, Color32 col)
    {
        int[] a = new int[sides], b = new int[sides];
        for (int k = 0; k < sides; k++)
        {
            float t = k * Mathf.PI * 2 / sides;
            float y = Mathf.Sin(t) * radius, z = Mathf.Cos(t) * radius;
            a[k] = Vertex(center + new Vector3(-width / 2, y, z), col);
            b[k] = Vertex(center + new Vector3(width / 2, y, z), col);
        }
        for (int k = 0; k < sides; k++)
        {
            int n = (k + 1) % sides;
            Quad(a[k], b[k], b[n], a[n]);
            if (k >= 1 && k < sides - 1) { Tri(a[0], a[k], a[k + 1]); Tri(b[0], b[k + 1], b[k]); }
        }
    }

    public Mesh ToMesh(string name)
    {
        var m = new Mesh { name = name, indexFormat = IndexFormat.UInt32 };
        m.SetVertices(V); m.SetColors(C); m.SetTriangles(T, 0);
        if (U.Count > 0) { while (U.Count < V.Count) U.Add(Vector3.zero); m.SetUVs(1, U); }
        m.RecalculateBounds();
        return m;
    }

    /// <summary>Ear-clipping triangulation of a CCW polygon (x,z). Returns triangle indices into pts.</summary>
    public static List<int> Triangulate(IList<Vector2> pts)
    {
        var idx = new List<int>();
        int n = pts.Count;
        var rem = new List<int>(n);
        for (int k = 0; k < n; k++) rem.Add(k);
        int guard = n * n + 10;
        while (rem.Count > 3 && guard-- > 0)
        {
            bool clipped = false;
            for (int k = 0; k < rem.Count; k++)
            {
                int ia = rem[(k + rem.Count - 1) % rem.Count], ib = rem[k], ic = rem[(k + 1) % rem.Count];
                Vector2 a = pts[ia], b = pts[ib], c = pts[ic];
                if (Cross(b - a, c - b) <= 1e-6f) continue;            // reflex or degenerate
                bool ear = true;
                foreach (int r in rem)
                {
                    if (r == ia || r == ib || r == ic) continue;
                    if (InTri(pts[r], a, b, c)) { ear = false; break; }
                }
                if (!ear) continue;
                idx.Add(ia); idx.Add(ib); idx.Add(ic);
                rem.RemoveAt(k); clipped = true; break;
            }
            if (!clipped) break;                                         // degenerate polygon: give up on the rest
        }
        if (rem.Count == 3) { idx.Add(rem[0]); idx.Add(rem[1]); idx.Add(rem[2]); }
        return idx;
    }
    static float Cross(Vector2 a, Vector2 b) => a.x * b.y - a.y * b.x;
    static bool InTri(Vector2 p, Vector2 a, Vector2 b, Vector2 c)
        => Cross(b - a, p - a) >= 0 && Cross(c - b, p - b) >= 0 && Cross(a - c, p - c) >= 0;
}
