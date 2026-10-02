using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using UnityEngine;

public class RoadData
{
    public float hw, realWidth, s0;      // hw: mean drawn half width; realWidth: surveyed carriageway width; s0: distance of the first point along its whole road link (keeps dashed lines in step)
    public bool dirt, bridge, lit, tunnel; public int fid, limit, avg, oneway, lanes, kind, rank;      // limit: legal speed km/h, avg: BD TOPO average km/h, oneway: 0 both ways, 1 along the line, 2 against it; fid: source feature
    public string name = ""; public string imp = "0";
    public int limitBack;                // BM07: legal speed against the line, km/h (OSM maxspeed:backward); 0 = same as limit
    public string surface = "";          // BM07: OSM surface (asphalt, gravel, sett, ...), else asphalt / dirt from the survey
    // BM07 lane markings (null in older chunks: ChunkMeshes falls back to rules on the road attributes)
    public byte[] lineKinds;             // per lane line: 0 centre line between the two directions, 1 divider between lanes of one direction
    public float[][] lineAcross;         // per lane line, per point: fraction of the way from the left edge to the right one; negative where not painted
    public byte[] marks;                 // per segment: bit 0 painted, bit 1 / 2 no overtaking along / against the line, bits 3-4 edge lines (0 none, 1 dashed, 2 solid)
    public float[] arrows = new float[0]; public int[] arrowBits = new int[0];      // turn arrows: x y z dx dz each; bits 1 left, 2 through, 4 right
    public float[] ribbon;               // BM07 embankment per point: left then right side, each shoulder width, toe distance / height, outer distance / height, apron heights (Ribbons.cs; null: none)
    public float[] pts, left, right;     // centreline and the two edges of the carriageway, x y z per point. The edges carry the cross slope.
    public int[] giveWayAt = new int[0]; public bool[] giveWayAfter = new bool[0];      // points where this road meets a junction as the minor road (a give-way line is painted); After: the drawn road lies after the point
    public bool[] drawn;                 // per segment: is it rendered and driven on? Not inside a junction: the junction surface covers it, the centreline stays for the traffic.
    Vector2[] xz; public Vector2 Min, Max;

    // ---- the polyline as 3D points with cumulative length (traffic follows it)
    Vector3[] own; float[] cum;
    void EnsureOwned()
    {
        if (own != null) return;
        own = new Vector3[pts.Length / 3]; cum = new float[own.Length];
        for (int i = 0; i < own.Length; i++) { own[i] = new Vector3(pts[i * 3], pts[i * 3 + 1], pts[i * 3 + 2]); if (i > 0) cum[i] = cum[i - 1] + Vector2.Distance(new Vector2(own[i].x, own[i].z), new Vector2(own[i - 1].x, own[i - 1].z)); }
    }
    public int Count => pts.Length / 3;
    public Vector3 P(float[] a, int i) => new Vector3(a[i * 3], a[i * 3 + 1], a[i * 3 + 2]);
    public float Length { get { EnsureOwned(); return cum[cum.Length - 1]; } }
    public Vector3 StartPoint { get { EnsureOwned(); return own[0]; } }
    public Vector3 EndPoint { get { EnsureOwned(); return own[own.Length - 1]; } }
    /// <summary>Point at distance s along the polyline (clamped) and the unit travel direction (x,z) of the segment there, in the direction of increasing s.</summary>
    public Vector3 At(float s, out Vector2 dir)
    {
        EnsureOwned(); s = Mathf.Clamp(s, 0f, cum[cum.Length - 1]);
        int i = System.Array.BinarySearch(cum, s); if (i < 0) i = ~i; i = Mathf.Clamp(i, 1, cum.Length - 1);
        float seg = Mathf.Max(cum[i] - cum[i - 1], 1e-4f), t = (s - cum[i - 1]) / seg;
        dir = new Vector2(own[i].x - own[i - 1].x, own[i].z - own[i - 1].z); dir = dir.sqrMagnitude > 1e-8f ? dir.normalized : Vector2.up;
        return Vector3.Lerp(own[i - 1], own[i], t);
    }
    /// <summary>Ground-plane polyline (x, z) of the road, cached; also fills Min / Max.</summary>
    public Vector2[] XZ
    {
        get
        {
            if (xz != null) return xz;
            xz = new Vector2[pts.Length / 3]; Min = new Vector2(1e9f, 1e9f); Max = new Vector2(-1e9f, -1e9f);
            for (int i = 0; i < xz.Length; i++) { xz[i] = new Vector2(pts[i * 3], pts[i * 3 + 2]); Min = Vector2.Min(Min, xz[i]); Max = Vector2.Max(Max, xz[i]); }
            return xz;
        }
    }
}
/// <summary>The surface of a junction: a small triangle mesh that meets the ends of its roads vertex for vertex.</summary>
public class JunctionData
{
    public bool dirt; public float[] v;      // x y z per vertex
    public int[] tri;                        // three vertex indices per triangle, clockwise seen from above
    public int[] edge; public bool[] mouth;  // outline: two vertex indices per edge, the surface on its left; mouth: the edge is where a road joins (no kerb there)
    public float[] ribbon;                   // BM07 per vertex: outward normal (x, z), then the embankment as for roads
}
/// <summary>BM07: one element of the lane graph (tools/roads/lanegraph.py): a polyline a car follows from start to end, then onto one of its successors.</summary>
public class LaneElem
{
    public const int Lane = 0, Change = 1, Connector = 2, UTurn = 3;
    public const int Priority = 0, GiveWay = 1, Stop = 2, Signals = 3, Right = 4;
    public int id, kind, control, limit, roadKind, imp, rank, junction, left = -1, right = -1;
    public bool dirt; public float hw, turn;
    public Vector3[] pts; public float[] cum, speed;          // speed: m/s allowed by the curvature and the limit at each point
    public int[] succ, yields;                                // successors; connectors this one gives way to
    public float Length => cum[cum.Length - 1];
    public Vector3 At(float s, out Vector2 dir)
    {
        s = Mathf.Clamp(s, 0f, Length);
        int i = System.Array.BinarySearch(cum, s); if (i < 0) i = ~i; i = Mathf.Clamp(i, 1, cum.Length - 1);
        float seg = Mathf.Max(cum[i] - cum[i - 1], 1e-4f), t = (s - cum[i - 1]) / seg;
        dir = new Vector2(pts[i].x - pts[i - 1].x, pts[i].z - pts[i - 1].z); dir = dir.sqrMagnitude > 1e-8f ? dir.normalized : Vector2.up;
        return Vector3.Lerp(pts[i - 1], pts[i], t);
    }
    /// <summary>Speed allowed at distance s (m/s), from the nearest points.</summary>
    public float SpeedAt(float s)
    {
        int i = System.Array.BinarySearch(cum, Mathf.Clamp(s, 0f, Length)); if (i < 0) i = ~i;
        return speed[Mathf.Clamp(i, 0, speed.Length - 1)];
    }
    /// <summary>Distance along the element of the point nearest to `p` (plan), searching all of it.</summary>
    public float Project(Vector2 p, out float dist)
    {
        float best = 1e9f, bestS = 0f;
        for (int i = 0; i + 1 < pts.Length; i++)
        {
            Vector2 a = new Vector2(pts[i].x, pts[i].z), b = new Vector2(pts[i + 1].x, pts[i + 1].z), ab = b - a;
            float t = Mathf.Clamp01(Vector2.Dot(p - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-6f)), d = (p - (a + ab * t)).sqrMagnitude;
            if (d < best) { best = d; bestS = cum[i] + t * (cum[i + 1] - cum[i]); }
        }
        dist = Mathf.Sqrt(best); return bestS;
    }
}
public class BuildingData
{
    public float[] p; public float b, h, r; public float[] rc; public int[] c; public int[] w; public string k, n; public int fe; public float[] tw; public float[] cp; public int[] cn;
    // BM07: the roof as the world builder made it (tools/roofs.py: straight skeleton over the footprint and its courtyards)
    public int roofMaterial = 255;           // BD TOPO roof material: 1 tiles, 2 slate, 3 zinc / metal, 4 concrete; 255 unknown
    public float[] roofV; public int[] roofT; public bool[] roofGable;      // vertices (x, height, z), triangles, gable-wall triangles
    // BM07: the facade as the world builder laid it out (tools/facades.py)
    public int seed;                         // from BD TOPO cleabs: the look stays when the outline moves
    public int floors, era, wallMaterial = 255;      // era 0 unknown, 1 before 1950, 2 1950 - 1970, 3 after; wall material 1 stone .. 6 wood, 9 other
    public float floorH;
    public FacadeWall[] walls;
}

public class Hedge { public float height; public float[] xz; }

/// <summary>A paved area that is not a road (a car park): vertices (x, y, z), triangles wound clockwise seen from above.</summary>
public class PavedArea
{
    public float[] v; public int[] t;
    public Vector3 V(int i) => new Vector3(v[i * 3], v[i * 3 + 1], v[i * 3 + 2]);

    /// <summary>Height of the surface at (x, z), NaN off it.</summary>
    public float Height(float x, float z)
    {
        for (int k = 0; k + 2 < t.Length; k += 3)
        {
            Vector3 a = V(t[k]), b = V(t[k + 1]), c = V(t[k + 2]);
            float area = (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x); if (Mathf.Abs(area) < 1e-6f) continue;
            float wb = ((x - a.x) * (c.z - a.z) - (z - a.z) * (c.x - a.x)) / area, wc = ((b.x - a.x) * (z - a.z) - (b.z - a.z) * (x - a.x)) / area;
            if (wb >= -1e-4f && wc >= -1e-4f && wb + wc <= 1f + 1e-4f) return a.y + wb * (b.y - a.y) + wc * (c.y - a.y);
        }
        return float.NaN;
    }
}

/// <summary>A wall of a building: `count` outline edges from point `first` (nearly collinear), its flags, the ground at both ends, its openings.</summary>
public class FacadeWall
{
    public const int Front = 1, Party = 2, Blind = 4, Back = 8;
    public int first, count, flags; public float g0, g1;
    public FacadeOpening[] openings;
}

/// <summary>A window or door: `t` metres along the wall from its first point (centre), its floor and kind, size, and its bottom above the local ground.</summary>
public struct FacadeOpening
{
    public const int Window = 0, Door = 1, Garage = 2, Shopfront = 3, Balcony = 4;
    public int floor, kind; public float t, width, height, sill;
}
[Serializable] public class WorldInfo { public float x0, z0; public int ncx, ncz; }
[Serializable] public class SpawnData { public float x, y, z, heading; public string road; }
public class WaterArea { public float level; public float[] ring; public float[] ys; }      // ring = x,z pairs; ys = surface height per vertex; level = their mean
public class WaterLine { public float hw; public int lead, trail; public float[] pts; }

/// <summary>One 400 m chunk as exported by tools/build_world.py: terrain grids, roads, junctions, water, buildings (mid data), trees and shrubs (near data).</summary>
public class ChunkData
{
    public int ci, cj, key; public float x0, z0;
    public float[] H;                 // CV x CV heights, row = z (south first)
    public byte[] Col;                // CV x CV rgb, bare-ground colour
    public byte[] LowCol;             // 26 x 26 rgb for the 16 m LOD (keeps forests and villages)
    public float[] LowH;              // LV x LV heights of the 16 m terrain (kept below the roads by the world builder)
    public RoadData[] Roads, Ctx;     // Ctx: roads of neighbouring chunks within 25 m (ground height and obstacle clearance near the border)
    public JunctionData[] Junctions, CtxJunctions;
    public WaterArea[] Areas; public WaterLine[] Lines;
    public WaterArea[] Troughs = new WaterArea[0];       // BM06: water carried by a structure (a canal on an aqueduct over a road): drawn with its channel, dry below
    public LaneElem[] Lanes = new LaneElem[0];           // BM07: the lane graph elements passing through this chunk (an element crossing chunks is in each)
    public bool[] Holes;                                 // BM07: per 4 m cell (row-major from the south-west), cut out of the terrain (tunnel portals, the road band); null when none
    public float[] Seam = new float[0];                  // BM07: the ground around the paved surfaces, triangles (x, y, z) x 3 (tools/stitch.py)
    public float[] SeamF = new float[0];                 // ... and per vertex the field's weight: 1 on a paved edge, 0 where it is the terrain
    public BuildingData[] Buildings;
    public float[] Trees, Shrubs;
    // BN02 (tools/ground.py): per tree its kind (0 unknown, 1 broadleaf, 2 conifer, 3 poplar, 4 fruit); per terrain vertex the ground
    // class (Ground.*) and the row direction (0 none, else 1 + 254 * angle / pi, angle from +x towards +z); vine rows (x0, z0, x1, z1);
    // parking bays (x, z, heading, 1 with a parked car); hedges (height, then x / z pairs)
    public byte[] TreeKind, GroundClass, RowDir;
    public float[] Vines = new float[0], Bays = new float[0];
    public Hedge[] Hedges = new Hedge[0];
    public PavedArea[] Parks = new PavedArea[0];        // car-park surfaces: drawn and driven like the roads
    public bool HasNear;
    public const int CV = WorldData.CV, LV = 26;

    /// <summary>Height of the ground as drawn at (x, z): in a cut cell the ground around the paved surfaces (BM07 seam), else the
    /// 4 m terrain triangle holding the point (two per cell, the diagonal alternating like ChunkMeshes builds them).</summary>
    public float Height(float x, float z)
    {
        float fx = Mathf.Clamp((x - x0) / WorldData.Cell, 0, CV - 1.001f), fz = Mathf.Clamp((z - z0) / WorldData.Cell, 0, CV - 1.001f);
        int ix = (int)fx, iz = (int)fz; float tx = fx - ix, tz = fz - iz;
        if (Holes != null && Seam.Length > 0 && Holes[iz * (CV - 1) + ix])
        {
            float y = SeamHeight(x, z, iz * (CV - 1) + ix);
            if (!float.IsNaN(y)) return y;
        }
        float a = H[iz * CV + ix], c = H[iz * CV + ix + 1], b = H[(iz + 1) * CV + ix], e = H[(iz + 1) * CV + ix + 1];      // a (x, z), c (x + 1, z), b (x, z + 1), e (x + 1, z + 1)
        if (((ix + iz) & 1) == 0) return tz >= tx ? a + (b - a) * tz + (e - b) * tx : a + (c - a) * tx + (e - c) * tz;        // diagonal a - e
        return tx + tz <= 1f ? a + (c - a) * tx + (b - a) * tz : e + (b - e) * (1f - tx) + (c - e) * (1f - tz);                // diagonal b - c
    }

    List<int>[] seamCells;                               // per 4 m cell, the seam triangles over it (built when the chunk is read)

    internal void IndexSeam()
    {
        seamCells = new List<int>[(CV - 1) * (CV - 1)];
        for (int t = 0; t * 9 + 8 < Seam.Length; t++)
        {
            float mnx = float.MaxValue, mxx = float.MinValue, mnz = float.MaxValue, mxz = float.MinValue;
            for (int q = 0; q < 3; q++) { float px = Seam[t * 9 + q * 3], pz = Seam[t * 9 + q * 3 + 2]; mnx = Mathf.Min(mnx, px); mxx = Mathf.Max(mxx, px); mnz = Mathf.Min(mnz, pz); mxz = Mathf.Max(mxz, pz); }
            int c0 = Mathf.Clamp((int)((mnx - x0) / WorldData.Cell), 0, CV - 2), c1 = Mathf.Clamp((int)((mxx - x0) / WorldData.Cell), 0, CV - 2);
            int r0 = Mathf.Clamp((int)((mnz - z0) / WorldData.Cell), 0, CV - 2), r1 = Mathf.Clamp((int)((mxz - z0) / WorldData.Cell), 0, CV - 2);
            for (int r = r0; r <= r1; r++) for (int c = c0; c <= c1; c++) (seamCells[r * (CV - 1) + c] ??= new List<int>()).Add(t);
        }
    }

    float SeamHeight(float x, float z, int cell)
    {
        var list = seamCells?[cell]; if (list == null) return float.NaN;
        foreach (int t in list)
        {
            int k = t * 9;
            float ax = Seam[k], az = Seam[k + 2], bx = Seam[k + 3], bz = Seam[k + 5], cx = Seam[k + 6], cz = Seam[k + 8];
            float det = (bx - ax) * (cz - az) - (bz - az) * (cx - ax); if (Mathf.Abs(det) < 1e-8f) continue;
            float wb = ((x - ax) * (cz - az) - (z - az) * (cx - ax)) / det, wc = ((bx - ax) * (z - az) - (bz - az) * (x - ax)) / det;
            if (wb >= -1e-4f && wc >= -1e-4f && wb + wc <= 1f + 1e-4f) return Seam[k + 1] + wb * (Seam[k + 4] - Seam[k + 1]) + wc * (Seam[k + 7] - Seam[k + 1]);
        }
        return float.NaN;
    }

    static string Str(BinaryReader br)
    {
        int n = 0, shift = 0; byte b;
        do { b = br.ReadByte(); n |= (b & 0x7F) << shift; shift += 7; } while ((b & 0x80) != 0);
        return n == 0 ? "" : System.Text.Encoding.UTF8.GetString(br.ReadBytes(n));
    }
    static float[] Floats(BinaryReader br, int n) { var a = new float[n]; var bytes = br.ReadBytes(n * 4); Buffer.BlockCopy(bytes, 0, a, 0, bytes.Length); return a; }
    static int[] UShorts(BinaryReader br, int n) { var a = new int[n]; var bytes = br.ReadBytes(n * 2); for (int i = 0; i < n; i++) a[i] = bytes[i * 2] | bytes[i * 2 + 1] << 8; return a; }
    public static float[] ReadFloats(BinaryReader br, int n) => Floats(br, n);
    public static string ReadString(BinaryReader br) => Str(br);
    static int[] Ints(BinaryReader br, int n) { var a = new int[n]; var bytes = br.ReadBytes(n * 4); Buffer.BlockCopy(bytes, 0, a, 0, bytes.Length); return a; }
    static BinaryReader Open(string path)
    {
        using (var fs = File.OpenRead(path))
        using (var gz = new GZipStream(fs, CompressionMode.Decompress))
        {
            var ms = new MemoryStream(); gz.CopyTo(ms); ms.Position = 0;
            return new BinaryReader(ms);
        }
    }

    /// <summary>Water polygons: per polygon its vertices (x, surface height, z).</summary>
    static WaterArea[] ReadWater(BinaryReader br)
    {
        var out_ = new WaterArea[br.ReadInt32()];
        for (int i = 0; i < out_.Length; i++)
        {
            int n = br.ReadInt32(); var t = Floats(br, n * 3); var a = new WaterArea { ring = new float[n * 2], ys = new float[n] };
            for (int k = 0; k < n; k++) { a.ring[k * 2] = t[k * 3]; a.ys[k] = t[k * 3 + 1]; a.ring[k * 2 + 1] = t[k * 3 + 2]; a.level += t[k * 3 + 1] / n; }
            out_[i] = a;
        }
        return out_;
    }

    public static ChunkData ParseMid(string path)
    {
        using (var br = Open(path))
        {
            string magic = new string(br.ReadChars(4));
            var d = new ChunkData { ci = br.ReadInt32(), cj = br.ReadInt32() };
            d.key = d.cj * WorldData.NCX + d.ci; d.x0 = WorldData.X0 + d.ci * WorldData.ChunkSize; d.z0 = WorldData.Z0 + d.cj * WorldData.ChunkSize;
            int cv = br.ReadInt32(); if (cv != CV) throw new InvalidDataException("terrain size " + cv);
            float baseH = br.ReadSingle(), step = WorldData.Legacy ? 0.005f : br.ReadSingle();      // the height range of a chunk sets the step (mountain chunks span hundreds of metres)
            d.H = new float[CV * CV]; var q = br.ReadBytes(CV * CV * 2);
            for (int i = 0; i < d.H.Length; i++) d.H[i] = baseH + (q[i * 2] | q[i * 2 + 1] << 8) * step;
            d.Col = br.ReadBytes(CV * CV * 3); d.LowCol = br.ReadBytes(LV * LV * 3);
            int version = magic.StartsWith("BM") && int.TryParse(magic.Substring(2), out int v) ? v : 0;
            if (version >= 5 && version <= 8)
            {
                d.LowH = Floats(br, LV * LV);
                d.Roads = ReadRoads(br, version); d.Ctx = ReadRoads(br, version);
                d.Junctions = ReadJunctions(br, version); d.CtxJunctions = ReadJunctions(br, version);
            }
            else if (magic == "BM02" || magic == "BM03" || magic == "BM04") LegacyChunk.ReadRoads(br, magic, d);      // worlds built before the road pipeline
            else throw new InvalidDataException(path);
            d.Areas = ReadWater(br);
            int nl = br.ReadInt32(); d.Lines = new WaterLine[nl];
            for (int i = 0; i < nl; i++) { var l = new WaterLine { hw = br.ReadSingle(), lead = br.ReadByte(), trail = br.ReadByte() }; int n = br.ReadInt32(); l.pts = Floats(br, n * 3); d.Lines[i] = l; }
            if (version >= 6) d.Troughs = ReadWater(br);
            if (version >= 7)
            {
                d.Lanes = ReadLanes(br);
                int nh = br.ReadInt32();
                if (nh > 0) { d.Holes = new bool[(CV - 1) * (CV - 1)]; var hb = br.ReadBytes(nh * 2); for (int k = 0; k < nh; k++) d.Holes[hb[k * 2] | hb[k * 2 + 1] << 8] = true; }
                if (version >= 8) ReadIndexedSeam(br, d);
                else { int ns = br.ReadInt32(); d.Seam = Floats(br, ns * 9); d.SeamF = Floats(br, ns * 3); }
                if (d.Seam.Length > 0) d.IndexSeam();
            }
            int nb = br.ReadInt32(); d.Buildings = new BuildingData[nb];
            for (int i = 0; i < nb; i++)
            {
                var b = new BuildingData(); int np = br.ReadInt32(); b.p = Floats(br, np * 2);
                b.b = br.ReadSingle(); b.h = br.ReadSingle(); b.r = br.ReadSingle();
                b.rc = Floats(br, br.ReadInt32());
                b.c = new[] { (int)br.ReadByte(), br.ReadByte(), br.ReadByte() }; b.w = new[] { (int)br.ReadByte(), br.ReadByte(), br.ReadByte() };
                b.k = Str(br); b.n = Str(br); b.fe = br.ReadInt32(); b.tw = Floats(br, br.ReadInt32());
                b.cp = Floats(br, br.ReadInt32() * 2); b.cn = Ints(br, br.ReadInt32());
                if (version >= 7)
                {
                    b.roofMaterial = br.ReadByte(); b.roofV = Floats(br, br.ReadInt32() * 3);
                    int nt = br.ReadInt32(); b.roofT = Ints(br, nt * 3); var g = br.ReadBytes(nt); b.roofGable = new bool[nt]; for (int k = 0; k < nt; k++) b.roofGable[k] = g[k] != 0;
                    b.seed = br.ReadInt32(); b.floors = br.ReadByte(); b.era = br.ReadByte(); b.wallMaterial = br.ReadByte(); b.floorH = br.ReadSingle();
                    b.walls = new FacadeWall[br.ReadInt32()];
                    for (int w = 0; w < b.walls.Length; w++)
                    {
                        var fw = new FacadeWall { first = br.ReadInt32(), count = br.ReadInt32(), flags = br.ReadByte(), g0 = br.ReadSingle(), g1 = br.ReadSingle() };
                        fw.openings = new FacadeOpening[br.ReadInt32()];
                        for (int o = 0; o < fw.openings.Length; o++)
                            fw.openings[o] = new FacadeOpening { floor = br.ReadByte(), kind = br.ReadByte(), t = br.ReadSingle(), width = br.ReadSingle(), height = br.ReadSingle(), sill = br.ReadSingle() };
                        b.walls[w] = fw;
                    }
                }
                d.Buildings[i] = b;
            }
            return d;
        }
    }

    /// <summary>BM08: the ground around the paved surfaces as shared vertices and triangles of vertex indices (tools/build_world.py
    /// put_seam): the columns x, y, z, field weight each as four byte planes, then the corner indices as differences from the previous
    /// one (int16 or int32). Expanded to the BM07 arrays the rest of the game reads.</summary>
    static void ReadIndexedSeam(BinaryReader br, ChunkData d)
    {
        int nv = br.ReadInt32();
        var col = new float[4][];
        for (int k = 0; k < 4; k++)
        {
            var planes = br.ReadBytes(nv * 4); var raw = new byte[nv * 4];
            for (int i = 0; i < nv; i++) for (int p = 0; p < 4; p++) raw[i * 4 + p] = planes[p * nv + i];
            col[k] = new float[nv]; Buffer.BlockCopy(raw, 0, col[k], 0, raw.Length);
        }
        int nt = br.ReadInt32(); int width = br.ReadByte();
        d.Seam = new float[nt * 9]; d.SeamF = new float[nt * 3];
        int at = 0;
        for (int k = 0; k < nt * 3; k++)
        {
            at += width == 2 ? br.ReadInt16() : br.ReadInt32();
            d.Seam[k * 3] = col[0][at]; d.Seam[k * 3 + 1] = col[1][at]; d.Seam[k * 3 + 2] = col[2][at]; d.SeamF[k] = col[3][at];
        }
    }

    static RoadData[] ReadRoads(BinaryReader br, int version)
    {
        int count = br.ReadInt32(); var a = new RoadData[count];
        for (int i = 0; i < count; i++)
        {
            var r = new RoadData(); byte fl = br.ReadByte(); r.dirt = (fl & 1) != 0; r.bridge = (fl & 2) != 0; r.lit = (fl & 4) != 0; r.tunnel = (fl & 8) != 0;
            r.imp = br.ReadByte().ToString(); r.limit = br.ReadByte(); r.avg = br.ReadByte(); r.oneway = br.ReadByte(); r.lanes = br.ReadByte(); r.kind = br.ReadByte(); r.rank = br.ReadByte();
            r.fid = br.ReadInt32(); r.realWidth = br.ReadSingle(); r.hw = br.ReadSingle(); r.s0 = br.ReadSingle();
            r.name = Str(br); int n = br.ReadInt32();
            r.pts = Floats(br, n * 3); r.left = Floats(br, n * 3); r.right = Floats(br, n * 3);
            var flags = br.ReadBytes(n - 1); r.drawn = new bool[n - 1]; for (int k = 0; k < n - 1; k++) r.drawn[k] = flags[k] != 0;
            int lines = br.ReadByte(); r.giveWayAt = new int[lines]; r.giveWayAfter = new bool[lines];
            for (int k = 0; k < lines; k++) { r.giveWayAt[k] = br.ReadUInt16(); r.giveWayAfter[k] = br.ReadByte() != 0; }
            if (version >= 7)
            {
                r.surface = Str(br); r.limitBack = br.ReadByte();
                r.lineKinds = br.ReadBytes(br.ReadByte()); r.lineAcross = new float[r.lineKinds.Length][];
                for (int k = 0; k < r.lineKinds.Length; k++) r.lineAcross[k] = Floats(br, n);
                r.marks = br.ReadBytes(n - 1);
                int na = br.ReadByte(); r.arrows = new float[na * 5]; r.arrowBits = new int[na];
                for (int k = 0; k < na; k++) { var f = Floats(br, 5); System.Array.Copy(f, 0, r.arrows, k * 5, 5); r.arrowBits[k] = br.ReadByte(); }
                if (br.ReadByte() != 0) r.ribbon = Floats(br, n * 2 * Ribbons.Profile);
            }
            else r.surface = r.dirt ? "dirt" : "asphalt";
            a[i] = r;
        }
        return a;
    }

    static LaneElem[] ReadLanes(BinaryReader br)
    {
        var a = new LaneElem[br.ReadInt32()];
        for (int i = 0; i < a.Length; i++)
        {
            var e = new LaneElem { id = br.ReadInt32(), kind = br.ReadByte(), control = br.ReadByte(), limit = br.ReadByte(), dirt = br.ReadByte() != 0, roadKind = br.ReadByte(), imp = br.ReadByte(), rank = br.ReadByte() };
            e.hw = br.ReadSingle(); e.turn = br.ReadSingle(); e.junction = br.ReadInt32();
            int n = br.ReadInt32(); var f = Floats(br, n * 3); e.pts = new Vector3[n]; e.cum = new float[n];
            for (int k = 0; k < n; k++) { e.pts[k] = new Vector3(f[k * 3], f[k * 3 + 1], f[k * 3 + 2]); if (k > 0) e.cum[k] = e.cum[k - 1] + Vector2.Distance(new Vector2(e.pts[k].x, e.pts[k].z), new Vector2(e.pts[k - 1].x, e.pts[k - 1].z)); }
            var sp = br.ReadBytes(n); e.speed = new float[n]; for (int k = 0; k < n; k++) e.speed[k] = sp[k] / 4f;
            e.succ = Ints(br, br.ReadByte()); e.left = br.ReadInt32(); e.right = br.ReadInt32(); e.yields = Ints(br, br.ReadByte());
            a[i] = e;
        }
        return a;
    }

    static JunctionData[] ReadJunctions(BinaryReader br, int version)
    {
        int count = br.ReadInt32(); var a = new JunctionData[count];
        for (int i = 0; i < count; i++)
        {
            var j = new JunctionData { dirt = br.ReadByte() != 0 };
            j.v = Floats(br, br.ReadInt32() * 3);
            j.tri = UShorts(br, br.ReadInt32() * 3);
            int ne = br.ReadInt32(); j.edge = UShorts(br, ne * 2);
            var flags = br.ReadBytes(ne); j.mouth = new bool[ne]; for (int k = 0; k < ne; k++) j.mouth[k] = flags[k] != 0;
            if (version >= 7) j.ribbon = Floats(br, j.v.Length / 3 * (Ribbons.Profile + 2));
            a[i] = j;
        }
        return a;
    }

    public void ParseNear(string path)
    {
        using (var br = Open(path))
        {
            string magic = new string(br.ReadChars(4));
            if (magic != "BN01" && magic != "BN02") throw new InvalidDataException(path);
            Trees = Floats(br, br.ReadInt32() * 4); Shrubs = Floats(br, br.ReadInt32() * 4);
            if (magic == "BN02")
            {
                TreeKind = br.ReadBytes(Trees.Length / 4);
                GroundClass = br.ReadBytes(CV * CV); RowDir = br.ReadBytes(CV * CV);
                Vines = Floats(br, br.ReadInt32() * 4); Bays = Floats(br, br.ReadInt32() * 4);
                Hedges = new Hedge[br.ReadInt32()];
                for (int i = 0; i < Hedges.Length; i++) { var h = new Hedge { height = br.ReadSingle() }; h.xz = Floats(br, br.ReadInt32() * 2); Hedges[i] = h; }
                Parks = new PavedArea[br.ReadInt32()];
                for (int i = 0; i < Parks.Length; i++) { var p = new PavedArea { v = Floats(br, br.ReadInt32() * 3) }; p.t = Ints(br, br.ReadInt32() * 3); Parks[i] = p; }
            }
        }
        HasNear = true;
    }
}

/// <summary>Everything the game knows about the world: the resident whole-map far terrain and the currently loaded chunks.</summary>
public class WorldData
{
    public const float Cell = 4f;
    public const int ChunkSize = 400, CV = 101;
    // world extents come from world.json; without one it is the original 32 x 32 km block (legacy format)
    public static float X0 = -16000f, Z0 = -16000f; public static int NCX = 80, NCZ = 80; public static bool Legacy = true;
    public static float MaxX => X0 + NCX * ChunkSize;
    public static float MaxZ => Z0 + NCZ * ChunkSize;
    public static bool InBounds(float x, float z, float margin = 0f) => x > X0 + margin && x < MaxX - margin && z > Z0 + margin && z < MaxZ - margin;
    public static Vector2 ChunkCenter(int ci, int cj) => new Vector2(X0 + (ci + 0.5f) * ChunkSize, Z0 + (cj + 0.5f) * ChunkSize);
    static string dir;
    /// <summary>Generated world: a "berat" folder beside the game (next to BeratRacer.exe, or next to BeratRacer.app: the map is a
    /// separate download), else StreamingAssets/berat inside a build, else ../world next to the Unity project in the editor.
    /// BERAT_WORLD overrides them all.</summary>
    public static string Dir
    {
        get
        {
            if (dir != null) return dir;
            var env = System.Environment.GetEnvironmentVariable("BERAT_WORLD");
            var beside = Path.GetFullPath(Application.platform == RuntimePlatform.OSXPlayer
                ? Path.Combine(Application.dataPath, "..", "..", "berat")           // dataPath: BeratRacer.app/Contents
                : Path.Combine(Application.dataPath, "..", "berat"));              // dataPath: BeratRacer_Data, beside the exe
            var built = Path.Combine(Application.streamingAssetsPath, "berat");
            var project = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "world"));
            foreach (var p in new[] { env, beside, built, project }) if (!string.IsNullOrEmpty(p) && File.Exists(Path.Combine(p, "far.bin"))) return dir = p;
            return dir = built;
        }
    }

    /// <summary>Folders holding chunk files: the world's own "chunks", then "chunks" of its numbered siblings ("berat-2", "berat-3",
    /// ...): a map downloaded in several parts, each part unzipped beside the others.</summary>
    public static List<string> ChunkDirs()
    {
        var dirs = new List<string> { Path.Combine(Dir, "chunks") };
        string root = Dir.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        for (int k = 2; Directory.Exists(Path.Combine(root + "-" + k, "chunks")); k++) dirs.Add(Path.Combine(root + "-" + k, "chunks"));
        return dirs;
    }

    public int FarNx, FarNz; public float FarCell;
    public float[] FarH; public byte[] FarC;
    public SpawnData Spawn;
    public readonly Dictionary<int, ChunkData> Chunks = new Dictionary<int, ChunkData>();

    public static WorldData Load()
    {
        var w = new WorldData();
        string wj = Path.Combine(Dir, "world.json");
        if (File.Exists(wj))
        {
            var info = JsonUtility.FromJson<WorldInfo>(File.ReadAllText(wj));
            if (info.ncx > 0) { X0 = info.x0; Z0 = info.z0; NCX = info.ncx; NCZ = info.ncz; Legacy = false; }
        }
        using (var br = new BinaryReader(File.OpenRead(Path.Combine(Dir, "far.bin"))))
        {
            if (Legacy) { w.FarNx = w.FarNz = br.ReadInt32(); w.FarCell = br.ReadSingle(); br.ReadSingle(); }
            else { w.FarNx = br.ReadInt32(); w.FarNz = br.ReadInt32(); w.FarCell = br.ReadSingle(); br.ReadSingle(); br.ReadSingle(); br.ReadSingle(); }
            var bytes = br.ReadBytes(w.FarNx * w.FarNz * 4); w.FarH = new float[w.FarNx * w.FarNz]; Buffer.BlockCopy(bytes, 0, w.FarH, 0, bytes.Length);
            w.FarC = br.ReadBytes(w.FarNx * w.FarNz * 3);
        }
        w.Spawn = JsonUtility.FromJson<SpawnData>(File.ReadAllText(Path.Combine(Dir, "spawn.json")));
        return w;
    }

    public static int ChunkIndexX(float x) => Mathf.FloorToInt((x - X0) / ChunkSize);
    public static int ChunkIndexZ(float z) => Mathf.FloorToInt((z - Z0) / ChunkSize);
    public static int ChunkKey(float x, float z) { int ci = ChunkIndexX(x), cj = ChunkIndexZ(z); return ci < 0 || cj < 0 || ci >= NCX || cj >= NCZ ? -1 : cj * NCX + ci; }

    /// <summary>Terrain height: the loaded chunk's 4 m grid (already carved under roads and water), else the coarse far terrain.</summary>
    public float TerrainHeight(float x, float z)
    {
        int k = ChunkKey(x, z);
        if (k >= 0 && Chunks.TryGetValue(k, out var c)) return c.Height(x, z);
        return FarHeight(x, z);
    }
    public bool HasChunk(float x, float z) { int k = ChunkKey(x, z); return k >= 0 && Chunks.ContainsKey(k); }

    public float FarHeight(float x, float z)
    {
        float fx = Mathf.Clamp((x - X0) / FarCell, 0, FarNx - 1.001f), fz = Mathf.Clamp((z - Z0) / FarCell, 0, FarNz - 1.001f);
        int ix = (int)fx, iz = (int)fz; float tx = fx - ix, tz = fz - iz;
        return Mathf.Lerp(Mathf.Lerp(FarH[iz * FarNx + ix], FarH[iz * FarNx + ix + 1], tx), Mathf.Lerp(FarH[(iz + 1) * FarNx + ix], FarH[(iz + 1) * FarNx + ix + 1], tx), tz);
    }

    // ---- snapshots of what is loaded (rebuilt lazily after chunks come and go)
    RoadData[] roads; BuildingData[] buildings; int version, roadsVersion = -1, buildingsVersion = -1;
    public void Changed() { version++; }
    public RoadData[] Roads
    {
        get
        {
            if (roadsVersion != version) { var l = new List<RoadData>(); foreach (var c in Chunks.Values) l.AddRange(c.Roads); roads = l.ToArray(); roadsVersion = version; }
            return roads;
        }
    }
    public BuildingData[] Buildings
    {
        get
        {
            if (buildingsVersion != version) { var l = new List<BuildingData>(); foreach (var c in Chunks.Values) l.AddRange(c.Buildings); buildings = l.ToArray(); buildingsVersion = version; }
            return buildings;
        }
    }
    public int LoadedRoadVersion => version;
    LaneGraph lanes; int lanesVersion = -1;
    /// <summary>The lane graph of the loaded chunks (empty for worlds built before BM07).</summary>
    public LaneGraph Lanes
    {
        get
        {
            if (lanesVersion != version) { lanes = new LaneGraph(Chunks.Values); lanesVersion = version; }
            return lanes;
        }
    }

    /// <summary>Level of the water surface at (x,z) if it lies on standing or running water, else NaN.</summary>
    public float WaterLevel(float x, float z)
    {
        int k = ChunkKey(x, z);
        if (k < 0 || !Chunks.TryGetValue(k, out var c)) return float.NaN;
        foreach (var t in c.Troughs) if (InRing(t.ring, x, z)) return float.NaN;          // under an aqueduct: dry
        foreach (var a in c.Areas) if (InRing(a.ring, x, z)) return a.level;
        foreach (var l in c.Lines)
        {
            var p = l.pts; float best = l.hw * l.hw;
            for (int i = 0; i + 5 < p.Length; i += 3)
            {
                Vector2 a = new Vector2(p[i], p[i + 2]), b = new Vector2(p[i + 3], p[i + 5]), ab = b - a, q = new Vector2(x, z);
                float t = Mathf.Clamp01(Vector2.Dot(q - a, ab) / Mathf.Max(ab.sqrMagnitude, 1e-4f));
                if ((q - (a + ab * t)).sqrMagnitude < best) return Mathf.Lerp(p[i + 1], p[i + 4], t);
            }
        }
        return float.NaN;
    }

    static bool InRing(float[] r, float x, float z)
    {
        bool inside = false; int n = r.Length / 2;
        for (int i = 0, j = n - 1; i < n; j = i++)
        {
            float xi = r[i * 2], zi = r[i * 2 + 1], xj = r[j * 2], zj = r[j * 2 + 1];
            if ((zi > z) != (zj > z) && x < (xj - xi) * (z - zi) / (zj - zi) + xi) inside = !inside;
        }
        return inside;
    }
}
