using System.Collections.Generic;
using UnityEngine;

/// <summary>Low-poly car bodies generated from a CarSpec: a Hot Hatch, a Peugeot 406 saloon and a Porsche 911 GT3 (992). Not reproductions, just the same idea.</summary>
public class CarVisualRefs { public Transform[] wheels; public GameObject brakeGlow, reverseGlow; }

public static class CarVisual
{
    static Color32 C(int r, int g, int b) => new Color32((byte)r, (byte)g, (byte)b, 255);
    static readonly Color32 Glass = new Color32(52, 70, 92, 24), Plastic = new Color32(30, 32, 36, 255), Lens = new Color32(238, 238, 228, 255),
        TailRed = new Color32(200, 24, 28, 255), Chrome = new Color32(196, 200, 206, 30), Tyre = new Color32(26, 26, 28, 255), Rim = new Color32(186, 190, 198, 255);

    public static CarVisualRefs Build(Transform car, Material mat, CarSpec sp)
    {
        var refs = new CarVisualRefs { wheels = new Transform[4] };
        var mb = new MeshBuilder();
        switch (sp.Style)
        {
            case BodyStyle.Sedan: Sedan(mb, sp); break;
            case BodyStyle.Sports: Sports(mb, sp); break;
            default: Hatch(mb, sp); break;
        }
        Attach(car, "body", mb.ToMesh("carBody"), mat);

        for (int i = 0; i < 4; i++)
        {
            var wb = new MeshBuilder(); float tw = sp.TyreWidth(i), R = sp.WheelR; float side = i % 2 == 0 ? -1f : 1f;
            wb.CylinderX(Vector3.zero, R, tw, 14, Tyre);
            float face = side * (tw * 0.5f + 0.004f);
            wb.CylinderX(new Vector3(face - side * 0.004f, 0, 0), R * 0.92f, 0.012f, 14, new Color32(44, 44, 48, 255));                       // sidewall band
            wb.CylinderX(new Vector3(face, 0, 0), R * 0.66f, 0.02f, 14, sp.Style == BodyStyle.Sports ? C(40, 40, 44) : Rim);
            for (int k = 0; k < 5; k++)
            {
                float a = k * 72f * Mathf.Deg2Rad; var dir = new Vector3(0, Mathf.Sin(a), Mathf.Cos(a));
                wb.BoxQ(new Vector3(face + side * 0.006f, dir.y * R * 0.33f, dir.z * R * 0.33f), new Vector3(0.028f, 0.05f, R * 0.5f), Quaternion.LookRotation(dir, Vector3.right), sp.Style == BodyStyle.Sports ? C(60, 60, 66) : Rim);
            }
            wb.CylinderX(new Vector3(face + side * 0.012f, 0, 0), R * 0.13f, 0.02f, 8, sp.Style == BodyStyle.Sports ? C(200, 30, 30) : C(120, 124, 130));   // hub / centre lock
            if (sp.Style == BodyStyle.Sports) wb.BoxQ(new Vector3(-side * (tw * 0.5f - 0.05f), R * 0.32f, R * 0.30f), new Vector3(0.06f, R * 0.5f, R * 0.32f), Quaternion.identity, C(230, 60, 40));   // brake caliper
            refs.wheels[i] = Attach(car, "wheel" + i, wb.ToMesh("wheel"), mat).transform;
        }
        // brake / reverse lamps: bright overlays that switch on with the pedals
        var g = new MeshBuilder(); var rv = new MeshBuilder();
        float zRear = sp.ZRear - 0.012f, yl = sp.GroundY + (sp.Style == BodyStyle.Sports ? 0.98f : 0.86f), hwL = sp.Width * (sp.Style == BodyStyle.Sports ? 0.30f : 0.36f);
        if (sp.Style == BodyStyle.Sports) g.Box(new Vector3(0, yl, zRear), new Vector3(sp.Width * 0.72f, 0.045f, 0.012f), C(255, 40, 40));
        else foreach (float s in new[] { -1f, 1f }) g.Box(new Vector3(s * hwL, yl, zRear), new Vector3(sp.Width * 0.20f, 0.09f, 0.012f), C(255, 46, 46));
        foreach (float s in new[] { -1f, 1f }) rv.Box(new Vector3(s * hwL * 0.55f, yl - 0.11f, zRear), new Vector3(0.10f, 0.05f, 0.012f), C(255, 255, 255));
        var glowMat = new Material(mat); glowMat.SetFloat("_Emission", 3.2f);
        refs.brakeGlow = Attach(car, "brakeGlow", g.ToMesh("brakeGlow"), glowMat); refs.reverseGlow = Attach(car, "reverseGlow", rv.ToMesh("reverseGlow"), glowMat);
        refs.brakeGlow.SetActive(false); refs.reverseGlow.SetActive(false);
        return refs;
    }

    static GameObject Attach(Transform parent, string name, Mesh m, Material mat)
    {
        var go = new GameObject(name); go.transform.SetParent(parent, false);
        go.AddComponent<MeshFilter>().sharedMesh = m;
        var mr = go.AddComponent<MeshRenderer>(); mr.sharedMaterial = mat;
        mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On; mr.receiveShadows = true;
        return go;
    }

    static Color32 Gloss(Color32 c) => new Color32(c.r, c.g, c.b, 45);        // vertex alpha = 255 - gloss: clear-coated paint

    // ---------------------------------------------------------------- shared helpers
    /// <summary>Point on a side silhouette. t: 0 = nose, 1 = tail; h: height above ground; hw: half width there.</summary>
    static Vector3 P(CarSpec sp, float t, float h, float hw) => new Vector3(sp.ZFront - t * (sp.ZFront - sp.ZRear), sp.GroundY + h, hw);
    static float Z(CarSpec sp, float t) => sp.ZFront - t * (sp.ZFront - sp.ZRear);
    static float Y(CarSpec sp, float h) => sp.GroundY + h;

    /// <summary>Hull outline: upper edge (nose -> tail), flat underside with a real wheel-arch cut-out around each axle.</summary>
    static List<Vector3> Hull(CarSpec sp, List<Vector3> top, float clearance, float hwBottom)
    {
        var pts = new List<Vector3>(top);
        float yb = Y(sp, clearance), yc = Y(sp, sp.WheelR), ra = sp.WheelR + 0.055f;
        pts.Add(new Vector3(sp.ZRear, yb, hwBottom));
        foreach (float zax in new[] { -sp.B, sp.A })
        {
            pts.Add(new Vector3(zax - ra, yb, hwBottom)); pts.Add(new Vector3(zax - ra, yc, hwBottom));
            for (int k = 5; k >= 1; k--) { float a = k * 30f * Mathf.Deg2Rad; pts.Add(new Vector3(zax + ra * Mathf.Cos(a), yc + ra * Mathf.Sin(a), hwBottom)); }
            pts.Add(new Vector3(zax + ra, yc, hwBottom)); pts.Add(new Vector3(zax + ra, yb, hwBottom));
        }
        pts.Add(new Vector3(sp.ZFront, yb, hwBottom));
        return pts;
    }


    /// <summary>A square-section bar from a to b (used for pillars that must follow the glass edge exactly).</summary>
    static void Strut(MeshBuilder mb, Vector3 a, Vector3 b, float thick, Color32 c)
    {
        Vector3 d = b - a; mb.BoxQ((a + b) * 0.5f, new Vector3(thick, thick, d.magnitude + thick * 0.5f), Quaternion.LookRotation(d, Vector3.up), c);
    }

    /// <summary>A and C pillars on both sides, from the glass corner points; optional B pillar in black between the doors.</summary>
    static void Pillars(MeshBuilder mb, CarSpec sp, Color32 paint, Vector3 a0, Vector3 a1, Vector3 c0, Vector3 c1, float bT, float bLow, float bHigh, float bHw)
    {
        foreach (float s in new[] { -1f, 1f })
        {
            Vector3 W(Vector3 v) => new Vector3(s * v.z, v.y, v.x);                       // P() returns silhouette coordinates (z along the car, y, half width)
            Strut(mb, W(a0), W(a1), 0.07f, paint);
            Strut(mb, W(c0), W(c1), 0.09f, paint);
            if (bT > 0f) mb.Box(new Vector3(s * bHw, Y(sp, (bLow + bHigh) * 0.5f), Z(sp, bT)), new Vector3(0.05f, bHigh - bLow, 0.07f), Plastic);
        }
    }

    static void Mirrors(MeshBuilder mb, CarSpec sp, float t, float h, float hw, Color32 paint)
    {
        foreach (float s in new[] { -1f, 1f })
        {
            mb.Box(new Vector3(s * (hw + 0.06f), Y(sp, h), Z(sp, t)), new Vector3(0.14f, 0.09f, 0.11f), paint);
            mb.Box(new Vector3(s * (hw + 0.005f), Y(sp, h - 0.03f), Z(sp, t) + 0.03f), new Vector3(0.05f, 0.05f, 0.05f), Plastic);
        }
    }

    static void DoorLines(MeshBuilder mb, CarSpec sp, float hw, float t0, float t1, float h0, float h1, int doors)
    {
        Color32 line = new Color32(20, 22, 26, 255);
        foreach (float s in new[] { -1f, 1f })
        {
            for (int d = 0; d <= doors; d++) { float t = Mathf.Lerp(t0, t1, d / (float)doors); mb.Box(new Vector3(s * (hw + 0.004f), Y(sp, (h0 + h1) / 2), Z(sp, t)), new Vector3(0.006f, h1 - h0, 0.012f), line); }
            for (int d = 0; d < doors; d++) { float t = Mathf.Lerp(t0, t1, (d + 0.82f) / doors); mb.Box(new Vector3(s * (hw + 0.01f), Y(sp, h1 - 0.10f), Z(sp, t)), new Vector3(0.012f, 0.025f, 0.12f), Chrome); }   // handles
        }
    }

    // ---------------------------------------------------------------- Peugeot 406 saloon
    static void Sedan(MeshBuilder mb, CarSpec sp)
    {
        Color32 paint = Gloss(sp.Paint); float hw = sp.Width * 0.5f;
        var top = new List<Vector3> {
            P(sp,0.000f,0.60f,hw*0.94f), P(sp,0.012f,0.76f,hw*0.97f), P(sp,0.060f,0.81f,hw*0.99f), P(sp,0.290f,0.93f,hw*1.00f), P(sp,0.640f,0.98f,hw*1.00f),
            P(sp,0.800f,1.00f,hw*1.00f), P(sp,0.960f,0.99f,hw*0.98f), P(sp,0.992f,0.87f,hw*0.96f), P(sp,1.000f,0.72f,hw*0.95f) };
        mb.Prism(Hull(sp, top, 0.17f, hw * 0.93f), paint);
        // greenhouse (tinted glass) and painted roof
        mb.Prism(new List<Vector3> { P(sp,0.300f,0.94f,hw*0.90f), P(sp,0.400f,1.33f,hw*0.75f), P(sp,0.580f,1.37f,hw*0.75f), P(sp,0.745f,1.00f,hw*0.88f) }, Glass);
        mb.Prism(new List<Vector3> { P(sp,0.398f,1.325f,hw*0.76f), P(sp,0.412f,1.385f,hw*0.72f), P(sp,0.575f,1.392f,hw*0.72f), P(sp,0.585f,1.325f,hw*0.76f) }, paint);
        // A / C pillars follow the glass edge; black B pillar
        Pillars(mb, sp, paint, P(sp,0.300f,0.94f,hw*0.90f), P(sp,0.400f,1.33f,hw*0.75f), P(sp,0.745f,1.00f,hw*0.88f), P(sp,0.580f,1.37f,hw*0.75f), 0.52f, 0.98f, 1.33f, hw*0.80f);
        // lower plastic strips, lights, grille, plates
        mb.Box(new Vector3(0, Y(sp, 0.30f), Z(sp, 0.004f)), new Vector3(hw * 1.76f, 0.16f, 0.03f), Plastic);
        mb.Box(new Vector3(0, Y(sp, 0.30f), Z(sp, 0.996f)), new Vector3(hw * 1.76f, 0.16f, 0.03f), Plastic);
        mb.Box(new Vector3(0, Y(sp, 0.66f), Z(sp, 0.002f)), new Vector3(0.62f, 0.10f, 0.03f), Plastic);                   // grille
        mb.Box(new Vector3(0, Y(sp, 0.66f), Z(sp, 0.0f) + 0.02f), new Vector3(0.10f, 0.06f, 0.03f), Chrome);              // lion badge stand-in
        foreach (float s in new[] { -1f, 1f })
        {
            mb.BoxQ(new Vector3(s * hw * 0.70f, Y(sp, 0.73f), Z(sp, 0.012f)), new Vector3(0.42f, 0.11f, 0.06f), Quaternion.Euler(0, s * 12f, 0), Lens);            // headlamps
            mb.BoxQ(new Vector3(s * hw * 0.78f, Y(sp, 0.90f), Z(sp, 0.984f)), new Vector3(0.40f, 0.12f, 0.06f), Quaternion.Euler(0, -s * 10f, 0), TailRed);         // tail lamps
            mb.Box(new Vector3(s * hw * 0.5f, Y(sp, 0.46f), Z(sp, 0.001f)), new Vector3(0.16f, 0.06f, 0.03f), Lens);                                         // fog lamps
        }
        mb.Box(new Vector3(0, Y(sp, 0.60f), Z(sp, 1.0f) - 0.005f), new Vector3(0.46f, 0.11f, 0.02f), C(232, 232, 226));    // rear plate
        mb.Box(new Vector3(0, Y(sp, 0.45f), Z(sp, 0.0f) + 0.005f), new Vector3(0.46f, 0.11f, 0.02f), C(232, 232, 226));
        Mirrors(mb, sp, 0.345f, 1.02f, hw * 0.92f, paint);
        DoorLines(mb, sp, hw, 0.34f, 0.70f, 0.45f, 0.93f, 2);
    }

    // ---------------------------------------------------------------- Hot hatch
    static void Hatch(MeshBuilder mb, CarSpec sp)
    {
        Color32 paint = Gloss(sp.Paint); float hw = sp.Width * 0.5f;
        var top = new List<Vector3> {
            P(sp,0.000f,0.58f,hw*0.94f), P(sp,0.015f,0.74f,hw*0.97f), P(sp,0.075f,0.79f,hw*0.99f), P(sp,0.265f,0.91f,hw*1.00f), P(sp,0.700f,0.97f,hw*1.00f),
            P(sp,0.940f,1.00f,hw*0.98f), P(sp,0.990f,0.90f,hw*0.96f), P(sp,1.000f,0.70f,hw*0.95f) };
        mb.Prism(Hull(sp, top, 0.16f, hw * 0.93f), paint);
        mb.Prism(new List<Vector3> { P(sp,0.255f,0.93f,hw*0.90f), P(sp,0.370f,1.36f,hw*0.75f), P(sp,0.690f,1.40f,hw*0.75f), P(sp,0.925f,1.03f,hw*0.86f) }, Glass);
        mb.Prism(new List<Vector3> { P(sp,0.368f,1.355f,hw*0.76f), P(sp,0.385f,1.425f,hw*0.72f), P(sp,0.700f,1.43f,hw*0.72f), P(sp,0.715f,1.355f,hw*0.76f) }, paint);
        Pillars(mb, sp, paint, P(sp,0.255f,0.93f,hw*0.90f), P(sp,0.370f,1.36f,hw*0.75f), P(sp,0.925f,1.03f,hw*0.86f), P(sp,0.690f,1.40f,hw*0.75f), 0.53f, 1.00f, 1.36f, hw*0.80f);
        mb.Box(new Vector3(0, Y(sp, 1.09f), Z(sp, 0.965f)), new Vector3(hw * 1.5f, 0.05f, 0.24f), Plastic);                    // roof spoiler
        mb.Box(new Vector3(0, Y(sp, 0.30f), Z(sp, 0.004f)), new Vector3(hw * 1.76f, 0.16f, 0.03f), Plastic);
        mb.Box(new Vector3(0, Y(sp, 0.30f), Z(sp, 0.996f)), new Vector3(hw * 1.76f, 0.16f, 0.03f), Plastic);
        mb.Box(new Vector3(0, Y(sp, 0.62f), Z(sp, 0.002f)), new Vector3(0.66f, 0.13f, 0.03f), Plastic);
        foreach (float s in new[] { -1f, 1f })
        {
            mb.BoxQ(new Vector3(s * hw * 0.70f, Y(sp, 0.72f), Z(sp, 0.014f)), new Vector3(0.38f, 0.12f, 0.06f), Quaternion.Euler(0, s * 14f, 0), Lens);
            mb.BoxQ(new Vector3(s * hw * 0.82f, Y(sp, 0.86f), Z(sp, 0.987f)), new Vector3(0.20f, 0.20f, 0.06f), Quaternion.identity, TailRed);
        }
        mb.Box(new Vector3(0, Y(sp, 0.62f), Z(sp, 1.0f) - 0.005f), new Vector3(0.46f, 0.11f, 0.02f), C(232, 232, 226));
        mb.Box(new Vector3(0, Y(sp, 0.42f), Z(sp, 0.0f) + 0.005f), new Vector3(0.46f, 0.11f, 0.02f), C(232, 232, 226));
        Mirrors(mb, sp, 0.30f, 1.03f, hw * 0.92f, paint);
        DoorLines(mb, sp, hw, 0.32f, 0.75f, 0.45f, 0.94f, 2);
    }

    // ---------------------------------------------------------------- Porsche 911 GT3 (992)
    static void Sports(MeshBuilder mb, CarSpec sp)
    {
        Color32 paint = Gloss(sp.Paint), black = C(22, 22, 26), carbon = new Color32(34, 36, 42, 90); float hw = sp.Width * 0.5f;
        var top = new List<Vector3> {
            P(sp,0.000f,0.44f,hw*0.84f), P(sp,0.010f,0.55f,hw*0.92f), P(sp,0.045f,0.63f,hw*0.97f), P(sp,0.150f,0.70f,hw*0.99f), P(sp,0.305f,0.78f,hw*1.00f),
            P(sp,0.560f,0.86f,hw*1.00f), P(sp,0.770f,0.90f,hw*1.02f), P(sp,0.905f,0.92f,hw*0.99f), P(sp,0.960f,0.88f,hw*0.94f), P(sp,0.992f,0.78f,hw*0.86f), P(sp,1.000f,0.64f,hw*0.80f) };
        mb.Prism(Hull(sp, top, 0.12f, hw * 0.90f), paint);
        // fastback greenhouse: the roof peaks over the driver and the rear window sweeps down almost to the tail
        mb.Prism(new List<Vector3> { P(sp,0.305f,0.80f,hw*0.90f), P(sp,0.420f,1.20f,hw*0.72f), P(sp,0.550f,1.279f,hw*0.70f), P(sp,0.680f,1.22f,hw*0.74f), P(sp,0.900f,0.93f,hw*0.90f) }, Glass);
        mb.Prism(new List<Vector3> { P(sp,0.420f,1.195f,hw*0.735f), P(sp,0.44f,1.28f,hw*0.70f), P(sp,0.560f,1.283f,hw*0.70f), P(sp,0.640f,1.235f,hw*0.735f) }, carbon);   // carbon roof
        foreach (float s in new[] { -1f, 1f })
        {   // pillars and front fender humps
            mb.Box(new Vector3(s * hw * 0.80f, Y(sp, 0.735f), Z(sp, 0.115f)), new Vector3(0.40f, 0.07f, 0.42f), paint);                  // raised front wing
            mb.CylinderZ(new Vector3(s * hw * 0.72f, Y(sp, 0.735f), Z(sp, 0.030f)), 0.085f, 0.09f, 12, Lens);                            // round headlamps
            mb.CylinderZ(new Vector3(s * hw * 0.72f, Y(sp, 0.735f), Z(sp, 0.030f) - 0.004f), 0.05f, 0.09f, 10, C(60, 64, 70));
            mb.Box(new Vector3(s * hw * 0.98f, Y(sp, 0.30f), Z(sp, 0.50f)), new Vector3(0.05f, 0.10f, 1.9f), black);                     // side skirts
            mb.Box(new Vector3(s * hw * 0.55f, Y(sp, 0.35f), Z(sp, 0.004f)), new Vector3(0.44f, 0.20f, 0.05f), black);                   // front intakes
            mb.Box(new Vector3(s * hw * 0.86f, Y(sp, 0.60f), Z(sp, 0.66f) + 0.4f), new Vector3(0.05f, 0.10f, 0.40f), black);             // rear-quarter intake
            mb.Box(new Vector3(s * hw * 0.28f, Y(sp, 0.30f), Z(sp, 0.99f)), new Vector3(0.36f, 0.16f, 0.07f), black);                    // diffuser
            mb.CylinderZ(new Vector3(s * hw * 0.24f, Y(sp, 0.42f), Z(sp, 1.0f) - 0.02f), 0.055f, 0.14f, 10, C(150, 152, 158));           // exhaust tips
        }
        mb.Box(new Vector3(0, Y(sp, 0.62f), Z(sp, 0.0f) + 0.02f), new Vector3(hw * 1.5f, 0.05f, 0.15f), carbon);                          // front splitter
        mb.Box(new Vector3(0, Y(sp, 0.34f), Z(sp, 0.001f)), new Vector3(hw * 1.28f, 0.16f, 0.05f), black);                                // lower intake
        mb.Box(new Vector3(0, Y(sp, 0.31f), Z(sp, 0.996f)), new Vector3(hw * 1.74f, 0.09f, 0.06f), carbon);
        mb.Box(new Vector3(0, Y(sp, 0.86f), Z(sp, 0.985f)), new Vector3(hw * 1.72f, 0.038f, 0.05f), C(120, 16, 20));                      // light bar (lens)
        Pillars(mb, sp, paint, P(sp,0.305f,0.80f,hw*0.90f), P(sp,0.420f,1.20f,hw*0.72f), P(sp,0.900f,0.93f,hw*0.90f), P(sp,0.680f,1.22f,hw*0.74f), 0f, 0f, 0f, 0f);
        // swan-neck rear wing
        float wz = Z(sp, 0.945f), wy = Y(sp, 1.30f);
        foreach (float s in new[] { -1f, 1f })
        {
            mb.BoxQ(new Vector3(s * 0.52f, Y(sp, 1.10f), wz + 0.05f), new Vector3(0.05f, 0.42f, 0.09f), Quaternion.Euler(-14f, 0, 0), carbon);   // uprights
            mb.Box(new Vector3(s * 0.93f, wy - 0.03f, wz - 0.10f), new Vector3(0.025f, 0.24f, 0.44f), carbon);                                   // end plates
        }
        mb.BoxQ(new Vector3(0, wy, wz - 0.08f), new Vector3(hw * 1.78f, 0.045f, 0.40f), Quaternion.Euler(-4f, 0, 0), carbon);                    // main plane
        mb.Box(new Vector3(0, wy - 0.01f, wz - 0.29f), new Vector3(hw * 1.78f, 0.06f, 0.03f), paint);                                             // Gurney flap in body colour
        Mirrors(mb, sp, 0.385f, 0.97f, hw * 0.92f, carbon);
        DoorLines(mb, sp, hw, 0.39f, 0.66f, 0.42f, 0.87f, 1);
    }
}
