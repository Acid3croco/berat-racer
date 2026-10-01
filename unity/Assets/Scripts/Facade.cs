using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Procedural facades: picks a style from the building type (house, shed, barn, shop, pharmacy, church, town hall...)
/// and lays windows, shutters, doors, shopfronts, awnings, signs, chimneys and church towers out along each wall
/// so they adapt to that building's own footprint (edge lengths, floors, front edge facing the road).
/// </summary>
public class FacadeStyle
{
    public string kind;
    public Color32 wall, plinth, shutter, frame, glass, door, sign, sill = new Color32(214, 208, 194, 255);
    public bool hasRoof; public Color32 roof;
    public int mode;                    // 0 windows+shutters, 1 windows, 2 arched, 3 blind, 4 wide strip windows, 5 shopfront
    public float bay = 3.3f, winW = 1.0f, winH = 1.35f, floorH = 3.0f;
    public bool awning, cross, chimney, flag, tower;
    public Color32 awningA, awningB;
}

public static class Facade
{
    static Color32 C(int r, int g, int b) => new Color32((byte)r, (byte)g, (byte)b, 255);
    static Color32 Tint(Color32 c, float f) => new Color32((byte)Mathf.Clamp(c.r * f, 0, 255), (byte)Mathf.Clamp(c.g * f, 0, 255), (byte)Mathf.Clamp(c.b * f, 0, 255), 255);
    static T Pick<T>(System.Random r, T[] a) => a[r.Next(a.Length)];
    static float F(System.Random r, float lo, float hi) => lo + (float)r.NextDouble() * (hi - lo);

    // Haute-Garonne / Comminges village houses: mostly rendered walls (crepi) in white-beige-cream, plus 'brique toulousaine' pink-orange brick
    // From photos of Berat (docs/berat-style-guide.md): ~30% exposed brique foraine, ~40% cream / pink / off-white render, ~20% white-grey modern, some ochre.
    static readonly Color32 Brick = C(214, 142, 108), BrickTrim = C(222, 128, 80);
    static readonly Color32[] HouseWalls = {
        C(232, 220, 200), C(232, 220, 200), C(217, 169, 143), C(239, 234, 224), C(236, 228, 208), C(226, 212, 184),      // render: cream, warm pink, off-white
        C(242, 240, 234), C(207, 203, 196), C(242, 240, 234),                                                              // modern white / light grey
        C(183, 154, 106) };                                                                                                // ochre
    static readonly Color32 TileTerracotta = C(200, 104, 60);
    public static Color32 RoofTile(Color32 ortho, System.Random r)
    {   // canal tiles: new #C8683C, aged #A5583A, dark / lichened #7A4A3A, each mixed a little with the weathered orthophoto colour
        double d = r.NextDouble();
        var t = d < 0.4 ? C(200, 104, 60) : d < 0.8 ? C(165, 88, 58) : C(122, 74, 58);
        t = Tint(t, F(r, 0.94f, 1.06f));
        return new Color32((byte)((t.r * 3 + ortho.r) / 4), (byte)((t.g * 3 + ortho.g) / 4), (byte)((t.b * 3 + ortho.b) / 4), 255);
    }

    static readonly Color32[] Shutters = { C(127, 160, 216), C(127, 160, 216), C(127, 160, 216), C(143, 166, 208), C(125, 163, 200), C(220, 218, 212), C(220, 218, 212), C(78, 143, 134), C(112, 84, 62), C(58, 61, 66) };   // dominant light blue, grey-white, teal, brown, dark grey   // white, greys, pale blue, sage, brown
    static readonly Color32[] Doors = { C(185, 128, 63), C(185, 128, 63), C(127, 160, 216), C(112, 84, 62), C(142, 64, 52), C(58, 61, 66), C(226, 222, 212) };   // varnished honey wood, blue, brown

    public static FacadeStyle Pick(BuildingData bd, System.Random r)
    {
        var s = new FacadeStyle { kind = bd.k ?? "house", glass = new Color32(84, 106, 128, 70), frame = C(244, 242, 236), plinth = C(150, 144, 134) };
        s.shutter = Pick(r, Shutters); s.door = Pick(r, Doors);
        switch (s.kind)
        {
            case "shed": s.wall = Pick(r, new[] { C(200, 196, 186), C(180, 170, 150), C(226, 220, 205), C(150, 154, 150) }); s.mode = 3; break;
            case "barn": s.wall = Pick(r, new[] { C(150, 156, 152), C(178, 160, 120), C(120, 96, 72), C(196, 190, 176) }); s.mode = 3; s.hasRoof = true; s.roof = Pick(r, new[] { C(122, 128, 134), C(150, 92, 74) }); break;
            case "industrial": s.wall = Pick(r, new[] { C(198, 202, 204), C(180, 186, 176), C(214, 208, 190) }); s.mode = 4; s.floorH = 4.5f; s.hasRoof = true; s.roof = C(140, 146, 152); s.frame = C(90, 94, 100); s.door = C(70, 96, 130); break;
            case "silo": s.wall = C(210, 210, 204); s.mode = 3; s.hasRoof = true; s.roof = C(170, 172, 170); break;
            case "greenhouse": s.wall = C(190, 220, 205); s.mode = 3; s.hasRoof = true; s.roof = C(205, 232, 218); break;
            case "church": case "chapel":
                s.wall = Tint(Brick, F(r, 0.95f, 1.08f)); s.mode = 2; s.floorH = 4.6f; s.door = C(150, 100, 56); s.glass = C(58, 72, 110); s.frame = C(216, 176, 130);
                s.tower = s.kind == "church"; break;
            case "townhall": s.wall = C(238, 230, 210); s.mode = 0; s.flag = true; s.shutter = C(96, 120, 150); s.door = C(60, 84, 120); s.winH = 1.7f; s.floorH = 3.6f; break;
            case "school": s.wall = C(228, 208, 172); s.mode = 4; s.floorH = 3.6f; s.frame = C(96, 120, 150); s.door = C(96, 120, 150); break;
            case "library": case "hall": case "clinic": s.wall = Pick(r, new[] { C(236, 232, 222), C(226, 214, 190) }); s.mode = 4; s.floorH = 3.5f; s.frame = C(80, 92, 104); s.door = C(60, 84, 120); s.cross = s.kind == "clinic"; s.sign = C(40, 160, 90); break;
            case "pharmacy": s.wall = C(238, 236, 228); s.mode = 5; s.cross = true; s.sign = C(30, 156, 84); s.awning = false; s.frame = C(210, 214, 216); break;
            case "bakery": s.wall = C(232, 214, 176); s.mode = 5; s.sign = C(224, 170, 70); s.awning = true; s.awningA = C(214, 60, 50); s.awningB = C(244, 236, 220); break;
            case "grocery": s.wall = C(236, 232, 222); s.mode = 5; s.sign = C(196, 40, 44); s.awning = true; s.awningA = C(44, 130, 70); s.awningB = C(244, 240, 230); break;
            case "restaurant": s.wall = Pick(r, new[] { C(226, 200, 170), C(208, 150, 120) }); s.mode = 5; s.sign = C(58, 48, 44); s.awning = true; s.awningA = C(150, 40, 40); s.awningB = C(240, 232, 214); break;
            case "bar": s.wall = Pick(r, new[] { C(210, 170, 140), C(232, 220, 196) }); s.mode = 5; s.sign = C(120, 30, 36); s.awning = true; s.awningA = C(40, 70, 110); s.awningB = C(240, 236, 226); break;
            case "post": s.wall = Tint(Brick, 1.05f); s.mode = 5; s.sign = C(255, 205, 0); s.frame = C(216, 210, 198); break;
            case "shop": s.wall = Pick(r, new[] { C(226, 226, 222), C(214, 216, 220), C(232, 220, 198) }); s.mode = 5; s.sign = Pick(r, new[] { C(60, 96, 156), C(70, 130, 84), C(176, 60, 50) }); s.awning = r.NextDouble() < 0.5; s.awningA = s.sign; s.awningB = C(240, 238, 230); s.floorH = 3.6f; break;
            default: // house
                bool brick = r.NextDouble() < 0.30;
                s.wall = brick ? Tint(Brick, F(r, 0.9f, 1.1f)) : Tint(Pick(r, HouseWalls), F(r, 0.96f, 1.03f));
                s.mode = 0; s.chimney = true; s.bay = F(r, 3.0f, 3.8f); s.winW = F(r, 0.9f, 1.15f);
                s.frame = brick ? C(216, 210, 198) : (r.NextDouble() < 0.6 ? Tint(BrickTrim, F(r, 0.95f, 1.05f)) : C(244, 242, 236));   // brick surrounds on render, pale limestone on brick
                if (r.NextDouble() < 0.3) s.mode = 1;               // some houses have no shutters (modern roller shutters)
                break;
        }
        if (s.kind == "house" && bd.walls != null) Finish(s, bd, r);
        if (s.kind != "house") s.wall = Tint(s.wall, F(r, 0.96f, 1.03f));
        if (s.sign.a == 0) s.sign = C(120, 120, 120);
        s.plinth = s.kind == "house" ? C(168, 160, 148) : Tint(s.wall, 0.72f);
        return s;
    }

    static readonly Color32 Stone = C(196, 182, 156), Millstone = C(178, 146, 104), Wood = C(146, 108, 74);

    /// <summary>BM07 houses: the wall finish from BD TOPO's wall material and the joinery from the era. Brick is the structure of 68% of
    /// the small map's houses, but the photos show it bare on about 30% of buildings: brick is left exposed when built before 1970 (14%
    /// of houses), rendered with brick surrounds after. Old houses have shutters, post-1970 ones roller shutters.</summary>
    static void Finish(FacadeStyle s, BuildingData bd, System.Random r)
    {
        bool old = bd.era == 1 || bd.era == 2;
        switch (bd.wallMaterial)
        {
            case 1: s.wall = Tint(Stone, F(r, 0.92f, 1.06f)); s.frame = C(222, 214, 198); break;
            case 2: s.wall = Tint(Millstone, F(r, 0.92f, 1.06f)); s.frame = C(222, 214, 198); break;
            case 3: case 5: s.wall = Tint(Pick(r, HouseWalls), F(r, 0.96f, 1.03f)); s.frame = C(244, 242, 236); break;
            case 4:
                if (old) { s.wall = Tint(Brick, F(r, 0.9f, 1.1f)); s.frame = C(216, 210, 198); }
                else { s.wall = Tint(Pick(r, HouseWalls), F(r, 0.96f, 1.03f)); s.frame = Tint(BrickTrim, F(r, 0.95f, 1.05f)); }
                break;
            case 6: s.wall = Tint(Wood, F(r, 0.9f, 1.1f)); s.frame = C(232, 226, 212); break;
        }
        if (bd.era == 1) s.mode = 0;
        else if (bd.era == 3) { s.mode = 1; s.frame = r.NextDouble() < 0.5 ? C(244, 242, 236) : C(70, 72, 76); }
    }

    // ---- geometry helpers: a point on wall edge (a -> a + d*t), lifted to height y, pushed off the wall by `off` along n
    struct Edge { public Vector2 a, d, n; public float len; public bool front; }
    static Vector3 P(Edge e, float t, float y, float off) => new Vector3(e.a.x + e.d.x * t + e.n.x * off, y, e.a.y + e.d.y * t + e.n.y * off);
    static void Q(MeshBuilder mb, Edge e, float t0, float t1, float y0, float y1, float off, Color32 c)
        => mb.Quad(mb.Vertex(P(e, t0, y0, off), c), mb.Vertex(P(e, t1, y0, off), c), mb.Vertex(P(e, t1, y1, off), c), mb.Vertex(P(e, t0, y1, off), c));

    /// <summary>A box standing on the wall: from wall offset o0 to o1 (front, top, bottom and both ends; the back is against the wall).</summary>
    static void Slab(MeshBuilder mb, Edge e, float t0, float t1, float y0, float y1, float o0, float o1, Color32 col)
    {
        int a = mb.Vertex(P(e, t0, y0, o0), col), b = mb.Vertex(P(e, t1, y0, o0), col), c = mb.Vertex(P(e, t1, y1, o0), col), d = mb.Vertex(P(e, t0, y1, o0), col);
        int a1 = mb.Vertex(P(e, t0, y0, o1), col), b1 = mb.Vertex(P(e, t1, y0, o1), col), c1 = mb.Vertex(P(e, t1, y1, o1), col), d1 = mb.Vertex(P(e, t0, y1, o1), col);
        mb.Quad(a1, b1, c1, d1); mb.Quad(d, c, c1, d1); mb.Quad(a, b, b1, a1); mb.Quad(a, d, d1, a1); mb.Quad(b, c, c1, b1);
    }

    /// <summary>A rectangular frame standing `depth` proud of the wall, with an opening whose reveal goes back to `glassOff`: front bars, inner reveal and outer sides.</summary>
    static void Ring(MeshBuilder mb, Edge e, float t0, float t1, float y0, float y1, float bar, float depth, float glassOff, Color32 col)
    {
        float[] ot = { t0, t1, t1, t0 }, oy = { y0, y0, y1, y1 }, it = { t0 + bar, t1 - bar, t1 - bar, t0 + bar }, iy = { y0 + bar, y0 + bar, y1 - bar, y1 - bar };
        int[] o0 = new int[4], o1 = new int[4], i1 = new int[4], ig = new int[4];
        for (int k = 0; k < 4; k++)
        {
            o0[k] = mb.Vertex(P(e, ot[k], oy[k], 0f), col); o1[k] = mb.Vertex(P(e, ot[k], oy[k], depth), col);
            i1[k] = mb.Vertex(P(e, it[k], iy[k], depth), col); ig[k] = mb.Vertex(P(e, it[k], iy[k], glassOff), col);
        }
        for (int k = 0; k < 4; k++)
        {
            int n = (k + 1) % 4;
            mb.Quad(o1[k], o1[n], i1[n], i1[k]);            // front bar
            mb.Quad(i1[k], i1[n], ig[n], ig[k]);            // reveal
            mb.Quad(o0[k], o0[n], o1[n], o1[k]);            // outer side
        }
    }

    const float GlassOff = 0.09f, FrameDepth = 0.14f, FrameBar = 0.07f;

    static void Window(MeshBuilder mb, Edge e, float tc, float y, float w, float h, FacadeStyle s, bool shutters, bool arched)
    {
        Ring(mb, e, tc - w / 2 - FrameBar, tc + w / 2 + FrameBar, y - FrameBar, y + h + FrameBar, FrameBar, FrameDepth, GlassOff, s.frame);
        Q(mb, e, tc - w / 2, tc + w / 2, y, y + h, GlassOff, s.glass);
        if (arched)
        {
            mb.Tri(mb.Vertex(P(e, tc - w / 2 - FrameBar, y + h, 0.04f), s.frame), mb.Vertex(P(e, tc + w / 2 + FrameBar, y + h, 0.04f), s.frame), mb.Vertex(P(e, tc, y + h + w * 0.75f + FrameBar * 1.6f, 0.04f), s.frame));
            mb.Tri(mb.Vertex(P(e, tc - w / 2, y + h, GlassOff), s.glass), mb.Vertex(P(e, tc + w / 2, y + h, GlassOff), s.glass), mb.Vertex(P(e, tc, y + h + w * 0.75f, GlassOff), s.glass));
        }
        else
        {
            Slab(mb, e, tc - 0.02f, tc + 0.02f, y, y + h, GlassOff, GlassOff + 0.04f, s.frame);                                             // central mullion
            Slab(mb, e, tc - w / 2 - 0.14f, tc + w / 2 + 0.14f, y + h + FrameBar, y + h + FrameBar + 0.10f, 0f, 0.17f, s.sill);           // lintel
        }
        Slab(mb, e, tc - w / 2 - 0.16f, tc + w / 2 + 0.16f, y - FrameBar - 0.07f, y - FrameBar, 0f, 0.2f, s.sill);                           // sill
        if (shutters)
        {
            float sw = w * 0.5f;
            Slab(mb, e, tc - w / 2 - sw - 0.05f, tc - w / 2 - 0.05f, y - 0.04f, y + h + 0.04f, 0f, 0.07f, s.shutter);
            Slab(mb, e, tc + w / 2 + 0.05f, tc + w / 2 + sw + 0.05f, y - 0.04f, y + h + 0.04f, 0f, 0.07f, s.shutter);
        }
    }

    static void Door(MeshBuilder mb, Edge e, float tc, float yg, float w, float h, FacadeStyle s, bool arched)
    {
        Ring(mb, e, tc - w / 2 - 0.1f, tc + w / 2 + 0.1f, yg, yg + h + 0.1f, 0.1f, 0.15f, 0.09f, s.frame);
        Q(mb, e, tc - w / 2, tc + w / 2, yg + 0.1f, yg + h, 0.09f, s.door);
        if (arched) mb.Tri(mb.Vertex(P(e, tc - w / 2, yg + h, 0.09f), s.door), mb.Vertex(P(e, tc + w / 2, yg + h, 0.09f), s.door), mb.Vertex(P(e, tc, yg + h + w * 0.7f, 0.09f), s.door));
        Slab(mb, e, tc - w / 2 - 0.25f, tc + w / 2 + 0.25f, yg - 0.02f, yg + 0.14f, 0f, 0.45f, s.sill);                                     // step
        Slab(mb, e, tc + w * 0.28f, tc + w * 0.34f, yg + 1.0f, yg + 1.1f, 0.09f, 0.15f, C(220, 200, 120));                                  // handle
    }

    public static void Build(BuildingData bd, List<Vector2> ring, FacadeStyle s, float yg, float top, MeshBuilder mb, System.Random rng, System.Func<float, float, float> roadClearance = null)
    {
        int n = ring.Count;
        Vector2 cen = Vector2.zero; foreach (var p in ring) cen += p; cen /= n;
        float wallH = top - yg;
        int floors = Mathf.Max(1, Mathf.RoundToInt((wallH - 0.4f) / s.floorH));
        if (s.mode == 3 && s.kind != "shed" && s.kind != "barn") floors = 0;
        int fe = Mathf.Clamp(bd.fe, 0, n - 1);

        if (bd.walls != null) BuildWalls(bd, ring, cen, s, top, mb);
        else for (int i = 0; i < n; i++)
        {
            Vector2 a = ring[i], b = ring[(i + 1) % n];
            float L = (b - a).magnitude; if (L < 1.4f) continue;
            Vector2 d = (b - a) / L, nrm = new Vector2(d.y, -d.x);                     // CCW polygon: outward is to the right
            if (Vector2.Dot(nrm, (a + b) * 0.5f - cen) < 0) nrm = -nrm;               // guard against odd winding
            var e = new Edge { a = a, d = d, n = nrm, len = L, front = i == fe };

            // plinth band + roof-line cornice (thin, slightly darker/lighter strips break up the flat wall)
            Slab(mb, e, 0, L, yg, yg + 0.55f, 0f, 0.07f, s.plinth);
            if (wallH > 3f) Slab(mb, e, 0, L, top - 0.25f, top, 0f, 0.22f, Tint(s.wall, 1.06f));

            if (s.kind == "silo" || s.kind == "greenhouse") continue;

            if (s.mode == 5) { ShopEdge(mb, e, s, yg, top, rng); continue; }
            if (s.mode == 3) { BlindEdge(mb, e, s, yg, top, rng); continue; }

            bool arched = s.mode == 2;
            bool strip = s.mode == 4;
            float margin = arched ? 1.2f : 0.85f;
            float bayW = strip ? Mathf.Max(3.6f, s.bay) : (arched ? 4.2f : s.bay);
            int bays = Mathf.Max(0, Mathf.FloorToInt((L - 2 * margin) / bayW + 0.5f));
            if (L < 2.6f) bays = 0;
            if (L >= 2.6f && bays == 0) bays = 1;
            float bw = bays > 0 ? (L - 2 * margin) / bays : 0;
            float w = strip ? Mathf.Min(bw - 0.6f, 2.6f) : (arched ? 0.95f : s.winW);
            float h = arched ? Mathf.Clamp(wallH * 0.42f, 2.6f, 5.2f) : (strip ? 1.5f : s.winH);
            bool doorHere = e.front;
            int doorBay = bays / 2;

            for (int f = 0; f < floors; f++)
            {
                float fy = yg + f * s.floorH;
                if (arched && f > 0) break;
                for (int k = 0; k < bays; k++)
                {
                    float tc = margin + (k + 0.5f) * bw + F(rng, -0.12f, 0.12f);
                    if (f == 0 && doorHere && (bays % 2 == 1 ? k == doorBay : k == doorBay - 1)) { if (!(bays % 2 == 0)) { Door(mb, e, tc, yg, arched ? 1.5f : 1.0f, arched ? 2.6f : 2.1f, s, arched); continue; } }
                    if (f == 0 && doorHere && bays % 2 == 0 && k == doorBay - 1) { Door(mb, e, tc, yg, 1.0f, 2.1f, s, false); continue; }
                    if (!e.front && f == 0 && rng.NextDouble() < 0.22) continue;         // blind ground-floor bays on side/back walls
                    if (!e.front && rng.NextDouble() < 0.08) continue;
                    float wy = arched ? fy + Mathf.Max(1.6f, wallH * 0.25f) : fy + (f == 0 ? 0.95f : 0.9f);
                    if (wy + h > top - 0.35f) continue;
                    Window(mb, e, tc, wy, w, h, s, s.mode == 0 && !strip, arched);
                }
                if (f > 0 && f < floors) Slab(mb, e, 0, L, fy - 0.08f, fy + 0.08f, 0f, 0.08f, Tint(s.wall, 0.92f));   // floor string course
            }
            if (e.front && bays == 0 && L >= 1.6f) Door(mb, e, L / 2, yg, 1.0f, 2.1f, s, false);
        }

        // roof-mounted extras
        if (bd.r > 0 && bd.rc != null && bd.rc.Length == 6)
        {
            Vector2 c = new Vector2(bd.rc[0], bd.rc[1]), u = new Vector2(bd.rc[2], bd.rc[3]);
            float roofY = top + bd.r;
            if (s.chimney && rng.NextDouble() < 0.75)
            {
                Vector2 cp = c + u * (bd.rc[4] * F(rng, -0.3f, 0.3f));
                mb.Box(new Vector3(cp.x, roofY - 0.1f, cp.y), new Vector3(0.55f, 1.3f, 0.55f), Tint(s.wall, 0.78f));
                mb.Box(new Vector3(cp.x, roofY + 0.6f, cp.y), new Vector3(0.7f, 0.12f, 0.7f), C(110, 108, 104));
            }
        }
        if (s.tower && bd.tw != null && bd.tw.Length == 3) OctTower(mb, new Vector2(bd.tw[0], bd.tw[1]), yg, yg + bd.tw[2], s);
        if (s.flag) Flag(mb, ring, fe, yg, top, roadClearance);
    }

    static Edge EdgeOf(List<Vector2> ring, int i, Vector2 cen, bool front)
    {
        Vector2 a = ring[i], b = ring[(i + 1) % ring.Count];
        float L = (b - a).magnitude; Vector2 d = L > 1e-6f ? (b - a) / L : Vector2.right, nrm = new Vector2(d.y, -d.x);      // CCW polygon: outward is to the right
        if (Vector2.Dot(nrm, (a + b) * 0.5f - cen) < 0) nrm = -nrm;
        return new Edge { a = a, d = d, n = nrm, len = L, front = front };
    }

    /// <summary>BM07: the walls and openings the world builder laid out (tools/facades.py). Each opening stands on the ground at its own
    /// place along the wall; an opening is drawn on the outline edge holding its centre, kept within that edge.</summary>
    static void BuildWalls(BuildingData bd, List<Vector2> ring, Vector2 cen, FacadeStyle s, float top, MeshBuilder mb)
    {
        int n = ring.Count;
        var edges = new List<Edge>(); var starts = new List<float>();
        foreach (var w in bd.walls)
        {
            edges.Clear(); starts.Clear(); float total = 0;
            for (int j = 0; j < w.count; j++) { var e = EdgeOf(ring, (w.first + j) % n, cen, (w.flags & FacadeWall.Front) != 0); edges.Add(e); starts.Add(total); total += e.len; }
            float Ground(float t) => Mathf.Lerp(w.g0, w.g1, total > 0 ? t / total : 0f);
            bool blind = (w.flags & FacadeWall.Blind) != 0;
            for (int j = 0; j < edges.Count; j++)
            {
                var e = edges[j]; if (e.len < 0.05f) continue;
                float gl = Mathf.Min(Ground(starts[j]), Ground(starts[j] + e.len)), gh = Mathf.Max(Ground(starts[j]), Ground(starts[j] + e.len));
                Slab(mb, e, 0, e.len, gl - 0.3f, gh + 0.55f, 0f, 0.07f, s.plinth);
                if (top - gh > 3f) Slab(mb, e, 0, e.len, top - 0.25f, top, 0f, 0.22f, Tint(s.wall, 1.06f));
                if (blind) continue;
                for (int f = 1; f < bd.floors; f++)
                {
                    float fy = gh + f * bd.floorH; if (fy > top - 0.6f) break;
                    Slab(mb, e, 0, e.len, fy - 0.08f, fy + 0.08f, 0f, 0.08f, Tint(s.wall, 0.92f));          // floor string course
                }
            }
            foreach (var o in w.openings)
            {
                int j = edges.Count - 1; while (j > 0 && starts[j] > o.t) j--;
                var e = edges[j];
                float half = Mathf.Min(o.width / 2, e.len / 2 - 0.15f); if (half < 0.2f) continue;
                float t = Mathf.Clamp(o.t - starts[j], half + 0.15f, e.len - half - 0.15f), g = Ground(o.t);
                switch (o.kind)
                {
                    case FacadeOpening.Door: Door(mb, e, t, g, half * 2, o.height, s, s.mode == 2); break;
                    case FacadeOpening.Garage: GarageDoor(mb, e, t, g, half * 2, o.height, s); break;
                    case FacadeOpening.Shopfront: Shopfront(mb, e, t - half, t + half, g, top, s); break;
                    case FacadeOpening.Balcony:
                        Window(mb, e, t, g + o.sill, half * 2, o.height, s, s.mode == 0, false);
                        Slab(mb, e, t - half - 0.35f, t + half + 0.35f, g + o.sill - 0.25f, g + o.sill - 0.1f, 0f, 0.9f, s.sill);                   // balcony slab
                        Slab(mb, e, t - half - 0.35f, t + half + 0.35f, g + o.sill + 0.8f, g + o.sill + 0.86f, 0.84f, 0.9f, C(60, 62, 66));      // railing
                        break;
                    default:
                        Window(mb, e, t, g + o.sill, half * 2, o.height, s, s.mode == 0, s.mode == 2);
                        if (s.mode == 1 && bd.era == 3) Slab(mb, e, t - half - 0.07f, t + half + 0.07f, g + o.sill + o.height + 0.07f, g + o.sill + o.height + 0.3f, 0f, 0.12f, s.frame);   // roller shutter box
                        break;
                }
            }
        }
    }

    static void GarageDoor(MeshBuilder mb, Edge e, float tc, float yg, float dw, float dh, FacadeStyle s)
    {
        Color32 dc = s.kind == "barn" ? C(96, 78, 62) : s.door;
        Ring(mb, e, tc - dw / 2 - 0.1f, tc + dw / 2 + 0.1f, yg, yg + dh + 0.1f, 0.1f, 0.16f, 0.09f, s.frame);
        Q(mb, e, tc - dw / 2, tc + dw / 2, yg + 0.1f, yg + dh, 0.09f, dc);
        for (int k = 1; k < 4; k++) Slab(mb, e, tc - dw / 2, tc + dw / 2, yg + dh * k / 4f - 0.03f, yg + dh * k / 4f + 0.03f, 0.09f, 0.14f, Tint(dc, 0.8f));   // door panels
    }

    // ---------------------------------------------------------------- special edges
    static void BlindEdge(MeshBuilder mb, Edge e, FacadeStyle s, float yg, float top, System.Random rng)
    {
        if (e.front && e.len > 2.6f)                                   // garage / barn door on the road-facing side
        {
            float dw = Mathf.Min(e.len - 1f, s.kind == "barn" ? 4.2f : 2.6f), dh = Mathf.Min(top - yg - 0.3f, s.kind == "barn" ? 3.6f : 2.2f);
            float tc = e.len * F(rng, 0.35f, 0.65f);
            Color32 dc = s.kind == "barn" ? C(96, 78, 62) : (rng.NextDouble() < 0.5 ? C(240, 238, 232) : C(112, 84, 62));
            Ring(mb, e, tc - dw / 2 - 0.1f, tc + dw / 2 + 0.1f, yg, yg + dh + 0.1f, 0.1f, 0.16f, 0.09f, s.frame);
            Q(mb, e, tc - dw / 2, tc + dw / 2, yg + 0.1f, yg + dh, 0.09f, dc);
            for (int k = 1; k < 4; k++) Slab(mb, e, tc - dw / 2, tc + dw / 2, yg + dh * k / 4f - 0.03f, yg + dh * k / 4f + 0.03f, 0.09f, 0.14f, Tint(dc, 0.8f));   // door panels
        }
        else if (e.len > 3.2f && rng.NextDouble() < 0.4)                // one small window
            Window(mb, e, e.len * 0.5f, yg + 1.2f, 0.7f, 0.7f, s, false, false);
    }

    static void ShopEdge(MeshBuilder mb, Edge e, FacadeStyle s, float yg, float top, System.Random rng)
    {
        float wallH = top - yg;
        if (!e.front)
        {   // side and back walls: ordinary windows
            int bays = Mathf.Max(0, Mathf.FloorToInt((e.len - 1.6f) / 3.4f + 0.5f)); if (e.len > 2.6f && bays == 0) bays = 1;
            float bw = bays > 0 ? (e.len - 1.6f) / bays : 0;
            int floors = Mathf.Max(1, Mathf.RoundToInt((wallH - 0.4f) / s.floorH));
            for (int f = 0; f < floors; f++)
                for (int k = 0; k < bays; k++)
                {
                    float wy = yg + f * s.floorH + 0.95f; if (wy + 1.35f > top - 0.3f) continue;
                    if (f == 0 && rng.NextDouble() < 0.4) continue;
                    Window(mb, e, 0.8f + (k + 0.5f) * bw, wy, 1.0f, 1.35f, s, false, false);
                }
            return;
        }
        Shopfront(mb, e, 0.5f, e.len - 0.5f, yg, top, s);
    }

    static void Shopfront(MeshBuilder mb, Edge e, float t0, float t1, float yg, float top, FacadeStyle s)
    {
        float wallH = top - yg;
        if (t1 - t0 < 1.5f) return;
        float dh = Mathf.Min(2.35f, wallH - 0.9f);
        Color32 glass = Tint(s.glass, 1.35f);
        float doorT = (t0 + t1) * 0.5f;
        Ring(mb, e, t0 - 0.08f, t1 + 0.08f, yg + 0.2f, yg + dh + 0.08f, 0.08f, 0.16f, 0.09f, s.frame);
        Q(mb, e, t0, doorT - 0.55f, yg + 0.3f, yg + dh, 0.09f, glass);
        Q(mb, e, doorT + 0.55f, t1, yg + 0.3f, yg + dh, 0.09f, glass);
        Slab(mb, e, doorT - 0.5f, doorT + 0.5f, yg, yg + dh, 0.08f, 0.13f, s.frame);
        Q(mb, e, doorT - 0.42f, doorT + 0.42f, yg + 0.1f, yg + dh - 0.1f, 0.19f, glass);
        if (wallH > 3.1f)
        {   // sign band across the top of the shopfront
            float sy0 = yg + dh + 0.12f, sy1 = Mathf.Min(top - 0.25f, sy0 + 0.75f);
            if (sy1 > sy0 + 0.3f)
            {
                Slab(mb, e, t0, t1, sy0, sy1, 0f, 0.16f, s.sign);
                Slab(mb, e, t0 + 0.15f, t1 - 0.15f, sy0 + 0.1f, sy1 - 0.1f, 0.16f, 0.21f, Tint(s.sign, 1.18f));
            }
            if (s.cross && sy1 + 0.2f < top)
            {   // pharmacy green cross (drawn on the wall above the sign when there is room, otherwise on the sign)
                float cy = Mathf.Min(top - 0.9f, sy1 + 0.6f);
                Slab(mb, e, doorT - 0.55f, doorT + 0.55f, cy - 0.18f, cy + 0.18f, 0f, 0.09f, s.sign);
                Slab(mb, e, doorT - 0.18f, doorT + 0.18f, cy - 0.55f, cy + 0.55f, 0f, 0.09f, s.sign);
            }
        }
        if (s.awning && wallH > 3.4f)
        {   // striped sloped awning
            float ay0 = yg + dh + 0.05f, ay1 = ay0 + 0.5f; int stripes = Mathf.Max(2, Mathf.FloorToInt((t1 - t0) / 0.6f));
            float sw = (t1 - t0) / stripes;
            for (int k = 0; k < stripes; k++)
            {
                var col = k % 2 == 0 ? s.awningA : s.awningB;
                float ta = t0 + k * sw, tb = ta + sw;
                mb.Quad(mb.Vertex(P(e, ta, ay0, 1.1f), col), mb.Vertex(P(e, tb, ay0, 1.1f), col), mb.Vertex(P(e, tb, ay1, 0.06f), col), mb.Vertex(P(e, ta, ay1, 0.06f), col));
            }
        }
    }

    /// <summary>Berat's Saint-Pierre: tall octagonal bell tower, cream render with brick bands, three tiers of arched openings, balustrade top, no spire.</summary>
    static void OctTower(MeshBuilder mb, Vector2 c, float yBase, float yTop, FacadeStyle s)
    {
        Color32 render = C(233, 228, 218), brick = BrickTrim, dark = C(46, 50, 62), cap = C(214, 208, 196);
        float R = 2.5f; int N = 8;
        Vector2 V(int k, float rad) { float a = k * Mathf.PI * 2 / N + Mathf.PI / N; return c + new Vector2(Mathf.Cos(a), Mathf.Sin(a)) * rad; }
        Vector3 W(Vector2 p, float y) => new Vector3(p.x, y, p.y);
        for (int k = 0; k < N; k++)
        {
            Vector2 p0 = V(k, R), p1 = V((k + 1) % N, R);
            mb.Quad(mb.Vertex(W(p0, yBase - 0.6f), render), mb.Vertex(W(p1, yBase - 0.6f), render), mb.Vertex(W(p1, yTop), render), mb.Vertex(W(p0, yTop), render));
            Vector2 mid = (p0 + p1) / 2, edge = (p1 - p0).normalized, nrm = (mid - c).normalized;
            void Face(float t0, float t1, float y0, float y1, float off, Color32 col)
            { Vector2 a0 = mid + edge * t0 + nrm * off, a1 = mid + edge * t1 + nrm * off; mb.Quad(mb.Vertex(W(a0, y0), col), mb.Vertex(W(a1, y0), col), mb.Vertex(W(a1, y1), col), mb.Vertex(W(a0, y1), col)); }
            float H = yTop - yBase;
            for (float by = yBase + 4.5f; by < yTop - 1f; by += 5.5f) Face(-1.0f, 1.0f, by, by + 0.5f, 0.05f, brick);     // brick bands
            for (int tier = 0; tier < 3; tier++)                                                                          // paired round-arched openings
            {
                float y0 = yTop - 3.4f - tier * 3.6f; if (y0 < yBase + 4f) continue;
                foreach (float tc in new[] { -0.4f, 0.4f })
                {
                    Face(tc - 0.22f, tc + 0.22f, y0, y0 + 1.5f, 0.06f, dark);
                    mb.Tri(mb.Vertex(W(mid + edge * (tc - 0.22f) + nrm * 0.06f, y0 + 1.5f), dark), mb.Vertex(W(mid + edge * (tc + 0.22f) + nrm * 0.06f, y0 + 1.5f), dark), mb.Vertex(W(mid + edge * tc + nrm * 0.06f, y0 + 1.95f), dark));
                }
                Face(-0.75f, 0.75f, y0 + 1.5f, y0 + 1.6f, 0.07f, brick);                                                   // arch course
            }
            Face(-1.05f, 1.05f, yTop - 1.0f, yTop - 0.05f, 0.12f, cap);                                                    // balustrade
            if (k == 0) { Face(-0.6f, 0.6f, yTop - 5.6f, yTop - 4.4f, 0.08f, C(245, 245, 240)); Face(-0.05f, 0.05f, yTop - 5.3f, yTop - 4.7f, 0.09f, dark); }   // clock face
        }
        int ctr = mb.Vertex(W(c, yTop + 0.1f), cap); var ring = new int[N];
        for (int k = 0; k < N; k++) ring[k] = mb.Vertex(W(V(k, R + 0.15f), yTop + 0.1f), cap);
        for (int k = 0; k < N; k++) mb.Tri(ctr, ring[k], ring[(k + 1) % N]);
    }

    static bool PointInRing(List<Vector2> r, Vector2 q)
    {
        bool inside = false;
        for (int i = 0, j = r.Count - 1; i < r.Count; j = i++)
            if ((r[i].y > q.y) != (r[j].y > q.y) && q.x < (r[j].x - r[i].x) * (q.y - r[i].y) / (r[j].y - r[i].y) + r[i].x) inside = !inside;
        return inside;
    }

    static void Flag(MeshBuilder mb, List<Vector2> ring, int fe, float yg, float top, System.Func<float, float, float> roadClearance)
    {
        Vector2 a = ring[fe], b = ring[(fe + 1) % ring.Count];
        Vector2 p = Vector2.Lerp(a, b, 0.5f), d = (b - a).normalized, nrm = new Vector2(d.y, -d.x);
        Vector2 cen = Vector2.zero; foreach (var q in ring) cen += q; cen /= ring.Count;
        if (Vector2.Dot(nrm, p - cen) < 0) nrm = -nrm;
        // stands on the ground beside the entrance: the first spot that is off every road (edge clearance >= 1.3 m) and outside the building; no flag if there is none
        Vector2 pole = default; bool found = false;
        foreach (var (front, along) in new[] { (1.6f, 3.2f), (1.6f, -3.2f), (1.2f, 5.5f), (1.2f, -5.5f), (0.8f, 8f), (0.8f, -8f), (0.7f, 0f), (0.5f, 11f), (0.5f, -11f) })
        {
            Vector2 c = p + nrm * front + d * along;
            if (roadClearance != null && roadClearance(c.x, c.y) < 1.3f) continue;
            if (PointInRing(ring, c)) continue;
            pole = c; found = true; break;
        }
        if (!found) return;
        float y0 = yg - 0.05f;
        mb.Box(new Vector3(pole.x, y0 + 3.3f, pole.y), new Vector3(0.09f, 6.6f, 0.09f), C(210, 210, 210));
        Color32[] fc = { C(0, 85, 164), C(245, 245, 245), C(239, 65, 53) };      // tricolore
        for (int k = 0; k < 3; k++)
        {
            Vector2 s0 = pole + d * (0.05f + 0.55f * k), s1 = pole + d * (0.05f + 0.55f * (k + 1));
            mb.Quad(mb.Vertex(new Vector3(s0.x, y0 + 6.5f, s0.y), fc[k]), mb.Vertex(new Vector3(s1.x, y0 + 6.5f, s1.y), fc[k]),
                    mb.Vertex(new Vector3(s1.x, y0 + 5.3f, s1.y), fc[k]), mb.Vertex(new Vector3(s0.x, y0 + 5.3f, s0.y), fc[k]));
        }
    }
}
