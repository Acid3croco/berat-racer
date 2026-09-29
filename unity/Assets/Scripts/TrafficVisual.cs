using System.Collections.Generic;
using UnityEngine;

public enum VehicleType { Car, Van, Truck, Semi }

/// <summary>What Traffic needs to move a vehicle: its parts, wheels to spin, collision boxes and how the body sits on the road.</summary>
public class VehicleParts
{
    public GameObject root, trailer;
    public Transform[] wheels = new Transform[0], trailerWheels = new Transform[0];
    public float[] wheelRadius = new float[0], trailerWheelRadius = new float[0];
    public float length = 4.5f, width = 1.8f, groundOffset;                  // groundOffset: height of the root origin above the road surface
    public float kingpin = 1.9f, trailerReach = 9.6f;                        // semi: kingpin behind the cab origin, kingpin -> trailer rear axle group
    public float wheelBaseMid;                                               // cars only: the wheels sit at the suspension mount plane
    public bool isCar; public CarSpec carSpec;
}

/// <summary>Low-poly vans, box trucks and articulated lorries for the traffic, built from boxes and a few prisms. Root origin: ground level, mid-wheelbase; z forward.</summary>
public static class TrafficVisual
{
    static Color32 C(int r, int g, int b) => new Color32((byte)r, (byte)g, (byte)b, 255);
    static readonly Color32 Glass = new Color32(52, 70, 92, 24), Plastic = new Color32(30, 32, 36, 255), Lens = new Color32(238, 238, 228, 255), TailRed = new Color32(200, 24, 28, 255),
        Tyre = new Color32(26, 26, 28, 255), Rim = new Color32(186, 190, 198, 255), Chassis = new Color32(44, 46, 50, 255);
    static Color32 Gloss(Color32 c) => new Color32(c.r, c.g, c.b, 45);

    static GameObject Part(Transform parent, string name, Mesh m, Material mat, bool cast = true)
    {
        var go = new GameObject(name); go.transform.SetParent(parent, false);
        go.AddComponent<MeshFilter>().sharedMesh = m;
        var mr = go.AddComponent<MeshRenderer>(); mr.sharedMaterial = mat;
        mr.shadowCastingMode = cast ? UnityEngine.Rendering.ShadowCastingMode.On : UnityEngine.Rendering.ShadowCastingMode.Off; mr.receiveShadows = true;
        return go;
    }

    static Transform Wheel(Transform parent, Material mat, Vector3 pos, float radius, float width, bool right)
    {
        var mb = new MeshBuilder(); float side = right ? 1f : -1f;
        mb.CylinderX(Vector3.zero, radius, width, 12, Tyre);
        mb.CylinderX(new Vector3(side * (width * 0.5f + 0.004f), 0, 0), radius * 0.62f, 0.02f, 12, Rim);
        mb.CylinderX(new Vector3(side * (width * 0.5f + 0.012f), 0, 0), radius * 0.14f, 0.02f, 6, C(120, 124, 130));
        var t = Part(parent, "wheel", mb.ToMesh("wheel"), mat).transform; t.localPosition = pos;
        return t;
    }

    static void Cab(MeshBuilder mb, Color32 paint, float zFront, float zBack, float wallY0, float roofY, float hw, float hood)
    {
        // side silhouette (x = z position, y = height, z = half width): nose, flat windscreen, roof
        var top = new List<Vector3> {
            new Vector3(zFront, wallY0, hw * 0.96f), new Vector3(zFront, hood, hw * 0.98f), new Vector3(zFront - 0.15f, hood + 0.55f, hw), new Vector3(zFront - 0.28f, roofY - 0.08f, hw),
            new Vector3(zFront - 0.42f, roofY, hw), new Vector3(zBack, roofY, hw), new Vector3(zBack, wallY0, hw) };
        mb.Prism(top, paint);
        // windscreen and side glass
        mb.BoxQ(new Vector3(0, (hood + roofY) * 0.5f + 0.08f, zFront - 0.17f), new Vector3(hw * 1.78f, roofY - hood - 0.55f, 0.03f), Quaternion.identity, Glass);
        foreach (float s in new[] { -1f, 1f }) mb.Box(new Vector3(s * (hw + 0.004f), roofY - 0.62f, (zFront + zBack) * 0.5f - 0.15f), new Vector3(0.012f, 0.62f, (zFront - zBack) * 0.55f), Glass);
        foreach (float s in new[] { -1f, 1f })
        {
            mb.Box(new Vector3(s * hw * 0.68f, wallY0 + (hood - wallY0) * 0.6f, zFront + 0.005f), new Vector3(hw * 0.38f, 0.16f, 0.02f), Lens);          // headlights
            mb.Box(new Vector3(s * (hw + 0.09f), roofY - 0.42f, zFront - 0.75f), new Vector3(0.05f, 0.30f, 0.16f), Plastic);                              // mirrors
        }
        mb.Box(new Vector3(0, wallY0 + 0.18f, zFront + 0.02f), new Vector3(hw * 2f * 0.98f, 0.22f, 0.10f), Plastic);                                        // bumper
    }

    static void Tail(MeshBuilder mb, float zBack, float y, float hw)
    {
        foreach (float s in new[] { -1f, 1f }) mb.Box(new Vector3(s * (hw - 0.22f), y, zBack - 0.005f), new Vector3(0.20f, 0.28f, 0.02f), TailRed);
    }

    // ---------------------------------------------------------------- van (Kangoo / Trafic class)
    public static VehicleParts Van(Material mat, Color32 paint)
    {
        var p = new VehicleParts { length = 4.95f, width = 1.92f, groundOffset = 0f, wheelBaseMid = 0f };
        var root = new GameObject("van"); p.root = root; float hw = 0.96f;
        var mb = new MeshBuilder(); paint = Gloss(paint);
        mb.Prism(new List<Vector3> {
            new Vector3(2.45f, 0.42f, hw * 0.94f), new Vector3(2.45f, 0.92f, hw * 0.96f), new Vector3(1.75f, 1.02f, hw), new Vector3(1.10f, 1.80f, hw), new Vector3(0.95f, 1.98f, hw),
            new Vector3(-2.45f, 1.98f, hw), new Vector3(-2.45f, 0.42f, hw) }, paint);
        mb.BoxQ(new Vector3(0, 1.45f, 1.42f), new Vector3(hw * 1.7f, 0.9f, 0.03f), Quaternion.Euler(-38f, 0, 0), Glass);                                    // windscreen
        foreach (float s in new[] { -1f, 1f })
        {
            mb.Box(new Vector3(s * (hw + 0.004f), 1.42f, 0.95f), new Vector3(0.012f, 0.58f, 1.0f), Glass);
            mb.Box(new Vector3(s * hw * 0.68f, 0.72f, 2.455f), new Vector3(hw * 0.36f, 0.14f, 0.02f), Lens);
            mb.Box(new Vector3(s * (hw + 0.09f), 1.38f, 1.55f), new Vector3(0.05f, 0.3f, 0.16f), Plastic);
        }
        mb.Box(new Vector3(0, 0.55f, 2.47f), new Vector3(hw * 1.96f, 0.22f, 0.10f), Plastic); mb.Box(new Vector3(0, 0.50f, -2.47f), new Vector3(hw * 1.96f, 0.20f, 0.10f), Plastic);
        Tail(mb, -2.45f, 0.9f, hw);
        Part(root.transform, "body", mb.ToMesh("vanBody"), mat);
        var w = new List<Transform>(); var r = new List<float>();
        foreach (var (z, front) in new[] { (1.5f, true), (-1.5f, false) })
            foreach (bool right in new[] { false, true }) { w.Add(Wheel(root.transform, mat, new Vector3((right ? 1f : -1f) * 0.84f, 0.33f, z), 0.33f, 0.22f, right)); r.Add(0.33f); }
        p.wheels = w.ToArray(); p.wheelRadius = r.ToArray();
        return p;
    }

    // ---------------------------------------------------------------- rigid box truck (12 t)
    public static VehicleParts BoxTruck(Material mat, Color32 cab, Color32 box)
    {
        var p = new VehicleParts { length = 9.2f, width = 2.5f, groundOffset = 0f };
        var root = new GameObject("truck"); p.root = root; float hw = 1.25f;
        var mb = new MeshBuilder();
        mb.Box(new Vector3(0, 0.78f, -0.4f), new Vector3(hw * 1.6f, 0.3f, 8.6f), Chassis);                                                                  // chassis
        Cab(mb, Gloss(cab), 4.55f, 2.35f, 0.55f, 3.0f, hw, 1.55f);
        mb.Box(new Vector3(0, 2.35f, -0.85f), new Vector3(hw * 2f, 2.6f, 6.6f), Gloss(box));                                                              // cargo box
        mb.Box(new Vector3(0, 0.98f, -0.85f), new Vector3(hw * 2f, 0.14f, 6.6f), Chassis);
        Tail(mb, -4.15f, 1.15f, hw);
        Part(root.transform, "body", mb.ToMesh("truckBody"), mat);
        var w = new List<Transform>(); var r = new List<float>();
        foreach (float z in new[] { 3.3f, -2.3f, -3.55f })
            foreach (bool right in new[] { false, true }) { w.Add(Wheel(root.transform, mat, new Vector3((right ? 1f : -1f) * 1.02f, 0.5f, z), 0.5f, 0.30f, right)); r.Add(0.5f); }
        p.wheels = w.ToArray(); p.wheelRadius = r.ToArray();
        return p;
    }

    // ---------------------------------------------------------------- articulated lorry: tractor + separate semi-trailer
    public static VehicleParts Semi(Material mat, Color32 cab, Color32 trailerPaint)
    {
        var p = new VehicleParts { length = 6.2f, width = 2.55f, groundOffset = 0f, kingpin = 1.7f, trailerReach = 10.15f };
        var root = new GameObject("tractor"); p.root = root; float hw = 1.275f;
        var mb = new MeshBuilder();
        mb.Box(new Vector3(0, 0.85f, -0.35f), new Vector3(hw * 1.5f, 0.3f, 5.4f), Chassis);
        Cab(mb, Gloss(cab), 3.05f, 0.85f, 0.7f, 3.3f, hw, 1.6f);
        mb.Box(new Vector3(0, 1.05f, -1.65f), new Vector3(hw * 1.7f, 0.22f, 2.3f), Chassis);                                                               // fifth-wheel deck
        Part(root.transform, "body", mb.ToMesh("tractorBody"), mat);
        var w = new List<Transform>(); var r = new List<float>();
        foreach (float z in new[] { 2.05f, -1.35f, -2.65f })
            foreach (bool right in new[] { false, true }) { w.Add(Wheel(root.transform, mat, new Vector3((right ? 1f : -1f) * 1.05f, 0.5f, z), 0.5f, 0.32f, right)); r.Add(0.5f); }
        p.wheels = w.ToArray(); p.wheelRadius = r.ToArray();

        var tr = new GameObject("trailer"); p.trailer = tr; float thw = 1.275f;
        var tb = new MeshBuilder();
        tb.Box(new Vector3(0, 2.4f, 0), new Vector3(thw * 2f, 2.7f, 13.4f), Gloss(trailerPaint));
        tb.Box(new Vector3(0, 0.98f, 0.5f), new Vector3(thw * 1.6f, 0.16f, 12.5f), Chassis);
        tb.Box(new Vector3(0, 0.6f, 5.2f), new Vector3(0.9f, 0.7f, 0.5f), Chassis);                                                                        // landing gear
        Tail(tb, -6.7f, 1.05f, thw);
        Part(tr.transform, "trailerBody", tb.ToMesh("trailerBody"), mat);
        var tw = new List<Transform>(); var trad = new List<float>();
        foreach (float z in new[] { -3.7f, -4.95f, -6.2f })
            foreach (bool right in new[] { false, true }) { tw.Add(Wheel(tr.transform, mat, new Vector3((right ? 1f : -1f) * 1.05f, 0.5f, z), 0.5f, 0.30f, right)); trad.Add(0.5f); }
        p.trailerWheels = tw.ToArray(); p.trailerWheelRadius = trad.ToArray();
        return p;
    }
}
