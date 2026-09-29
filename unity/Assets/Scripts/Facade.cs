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
        if (s.kind != "house") s.wall = Tint(s.wall, F(r, 0.96f, 1.03f));
        if (s.sign.a == 0) s.sign = C(120, 120, 120);
        s.plinth = s.kind == "house" ? C(168, 160, 148) : Tint(s.wall, 0.72f);
        return s;
    }

    // ---- geometry helpers: a point on wall edge (a -> a + d*t), lifted to height y, pushed off the wall by `off` along n
    struct Edge { public Vector2 a, d, n; public float len; public bool front; }
    static Vector3 P(Edge e, float t, float y, float off) => new Vector3(e.a.x + e.d.x * t + e.n.x * off, y, e.a.y + e.d.y * t + e.n.y * off);
    static void Q(MeshBuilder mb, Edge e, float t0, float t1, float y0, float y1, float off, Color32 c)
        => mb.Quad(mb.Vertex(P(e, t0, y0, off), c), mb.Vertex(P(e, t1, y0, off), c), mb.Vertex(P(e, t1, y1, off), c), mb.Vertex(P(e, t0, y1, off), c));

    static void Window(MeshBuilder mb, Edge e, float tc, float y, float w, float h, FacadeStyle s, bool shutters, bool arched)
    {
        Q(mb, e, tc - w / 2 - 0.07f, tc + w / 2 + 0.07f, y - 0.07f, y + h + 0.07f, 0.02f, s.frame);
        Q(mb, e, tc - w / 2, tc + w / 2, y, y + h, 0.035f, s.glass);
        if (arched) mb.Tri(mb.Vertex(P(e, tc - w / 2, y + h, 0.035f), s.glass), mb.Vertex(P(e, tc + w / 2, y + h, 0.035f), s.glass), mb.Vertex(P(e, tc, y + h + w * 0.75f, 0.035f), s.glass));
        else Q(mb, e, tc - 0.02f, tc + 0.02f, y, y + h, 0.045f, s.frame);                 // central mullion
        Q(mb, e, tc - w / 2 - 0.15f, tc + w / 2 + 0.15f, y - 0.1f, y - 0.01f, 0.06f, s.sill);
        if (shutters)
        {
            float sw = w * 0.5f;
            Q(mb, e, tc - w / 2 - sw - 0.03f, tc - w / 2 - 0.03f, y - 0.02f, y + h + 0.02f, 0.05f, s.shutter);
            Q(mb, e, tc + w / 2 + 0.03f, tc + w / 2 + sw + 0.03f, y - 0.02f, y + h + 0.02f, 0.05f, s.shutter);
        }
    }

    static void Door(MeshBuilder mb, Edge e, float tc, float yg, float w, float h, FacadeStyle s, bool arched)
    {
        Q(mb, e, tc - w / 2 - 0.1f, tc + w / 2 + 0.1f, yg, yg + h + 0.1f, 0.02f, s.frame);
        Q(mb, e, tc - w / 2, tc + w / 2, yg, yg + h, 0.04f, s.door);
        if (arched) mb.Tri(mb.Vertex(P(e, tc - w / 2, yg + h, 0.04f), s.door), mb.Vertex(P(e, tc + w / 2, yg + h, 0.04f), s.door), mb.Vertex(P(e, tc, yg + h + w * 0.7f, 0.04f), s.door));
        Q(mb, e, tc - w / 2 - 0.2f, tc + w / 2 + 0.2f, yg - 0.05f, yg + 0.12f, 0.35f, s.sill);   // step
        Q(mb, e, tc + w * 0.28f, tc + w * 0.34f, yg + 1.0f, yg + 1.1f, 0.055f, C(220, 200, 120)); // handle
    }

    public static void Build(BuildingData bd, List<Vector2> ring, FacadeStyle s, float yg, float top, MeshBuilder mb, System.Random rng)
    {
        int n = ring.Count;
        Vector2 cen = Vector2.zero; foreach (var p in ring) cen += p; cen /= n;
        float wallH = top - yg;
        int floors = Mathf.Max(1, Mathf.RoundToInt((wallH - 0.4f) / s.floorH));
        if (s.mode == 3 && s.kind != "shed" && s.kind != "barn") floors = 0;
        int fe = Mathf.Clamp(bd.fe, 0, n - 1);

        for (int i = 0; i < n; i++)
        {
            Vector2 a = ring[i], b = ring[(i + 1) % n];
            float L = (b - a).magnitude; if (L < 1.4f) continue;
            Vector2 d = (b - a) / L, nrm = new Vector2(d.y, -d.x);                     // CCW polygon: outward is to the right
            if (Vector2.Dot(nrm, (a + b) * 0.5f - cen) < 0) nrm = -nrm;               // guard against odd winding
            var e = new Edge { a = a, d = d, n = nrm, len = L, front = i == fe };

            // plinth band + roof-line cornice (thin, slightly darker/lighter strips break up the flat wall)
            Q(mb, e, 0, L, yg, yg + 0.55f, 0.02f, s.plinth);
            if (wallH > 3f) Q(mb, e, 0, L, top - 0.2f, top, 0.03f, Tint(s.wall, 1.06f));

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
                    if (fy + wy - fy + h > wallH + 0.05f && f > 0) continue;
                    if (wy + h > top - 0.35f) continue;
                    Window(mb, e, tc, wy, w, h, s, s.mode == 0 && !strip, arched);
                }
                if (f > 0 && f < floors) Q(mb, e, 0, L, fy - 0.08f, fy + 0.08f, 0.03f, Tint(s.wall, 0.92f));   // floor string course
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
        if (s.flag) Flag(mb, ring, fe, yg, top);
    }

    // ---------------------------------------------------------------- special edges
    static void BlindEdge(MeshBuilder mb, Edge e, FacadeStyle s, float yg, float top, System.Random rng)
    {
        if (e.front && e.len > 2.6f)                                   // garage / barn door on the road-facing side
        {
            float dw = Mathf.Min(e.len - 1f, s.kind == "barn" ? 4.2f : 2.6f), dh = Mathf.Min(top - yg - 0.3f, s.kind == "barn" ? 3.6f : 2.2f);
            float tc = e.len * F(rng, 0.35f, 0.65f);
            Color32 dc = s.kind == "barn" ? C(96, 78, 62) : (rng.NextDouble() < 0.5 ? C(240, 238, 232) : C(112, 84, 62));
            Q(mb, e, tc - dw / 2 - 0.08f, tc + dw / 2 + 0.08f, yg, yg + dh + 0.08f, 0.02f, s.frame);
            Q(mb, e, tc - dw / 2, tc + dw / 2, yg, yg + dh, 0.04f, dc);
            for (int k = 1; k < 4; k++) Q(mb, e, tc - dw / 2, tc + dw / 2, yg + dh * k / 4f - 0.015f, yg + dh * k / 4f + 0.015f, 0.05f, Tint(dc, 0.8f));   // door panels
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
        float t0 = 0.5f, t1 = e.len - 0.5f;
        if (t1 - t0 < 1.5f) return;
        float dh = Mathf.Min(2.35f, wallH - 0.9f);
        Color32 glass = Tint(s.glass, 1.35f);
        float doorT = e.len * 0.5f;
        Q(mb, e, t0 - 0.08f, t1 + 0.08f, yg + 0.2f, yg + dh + 0.08f, 0.02f, s.frame);
        Q(mb, e, t0, doorT - 0.55f, yg + 0.3f, yg + dh, 0.04f, glass);
        Q(mb, e, doorT + 0.55f, t1, yg + 0.3f, yg + dh, 0.04f, glass);
        Q(mb, e, doorT - 0.5f, doorT + 0.5f, yg, yg + dh, 0.04f, s.frame);
        Q(mb, e, doorT - 0.42f, doorT + 0.42f, yg + 0.1f, yg + dh - 0.1f, 0.05f, glass);
        if (wallH > 3.1f)
        {   // sign band across the top of the shopfront
            float sy0 = yg + dh + 0.12f, sy1 = Mathf.Min(top - 0.25f, sy0 + 0.75f);
            if (sy1 > sy0 + 0.3f)
            {
                Q(mb, e, t0, t1, sy0, sy1, 0.05f, s.sign);
                Q(mb, e, t0 + 0.15f, t1 - 0.15f, sy0 + 0.1f, sy1 - 0.1f, 0.06f, Tint(s.sign, 1.18f));
            }
            if (s.cross && sy1 + 0.2f < top)
            {   // pharmacy green cross (drawn on the wall above the sign when there is room, otherwise on the sign)
                float cy = Mathf.Min(top - 0.9f, sy1 + 0.6f);
                Q(mb, e, doorT - 0.55f, doorT + 0.55f, cy - 0.18f, cy + 0.18f, 0.07f, s.sign);
                Q(mb, e, doorT - 0.18f, doorT + 0.18f, cy - 0.55f, cy + 0.55f, 0.07f, s.sign);
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

    static void Flag(MeshBuilder mb, List<Vector2> ring, int fe, float yg, float top)
    {
        Vector2 a = ring[fe], b = ring[(fe + 1) % ring.Count];
        Vector2 p = Vector2.Lerp(a, b, 0.5f), d = (b - a).normalized, nrm = new Vector2(d.y, -d.x);
        Vector2 cen = Vector2.zero; foreach (var q in ring) cen += q; cen /= ring.Count;
        if (Vector2.Dot(nrm, p - cen) < 0) nrm = -nrm;
        Vector2 pole = p + nrm * 1.6f + d * 3.2f;                      // stands on the ground beside the entrance
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
