using UnityEngine;

/// <summary>Builds the low-poly hot-hatch: body meshes plus four separate wheel objects.</summary>
public static class CarVisual
{
    /// <summary>Global car scale. 1.0 = 1.80 m wide x 4.25 m long (VW Golf class).</summary>
    public const float S = 0.92f;   // ~1.66 x 3.91 m, Renault Clio / Peugeot 208 class: fits Bérat's narrow roads
    public static Transform[] Build(Transform car, Material mat)
    {
        Color32 red = new Color32(214, 48, 44, 255), dark = new Color32(34, 38, 48, 255), glass = new Color32(70, 92, 118, 255),
            tyre = new Color32(28, 28, 30, 255), rim = new Color32(190, 192, 198, 255), lamp = new Color32(252, 244, 200, 255), tail = new Color32(255, 40, 30, 255);
        var body = new MeshBuilder();
        body.Box(new Vector3(0, -0.2f, 0) * S, new Vector3(1.8f, 0.55f, 4.25f) * S, red);
        body.Box(new Vector3(0, -0.42f, 0) * S, new Vector3(1.7f, 0.12f, 4.1f) * S, dark);                        // under-tray
        body.Box(new Vector3(0, 0.34f, -0.25f) * S, new Vector3(1.62f, 0.52f, 2.1f) * S, glass, new Vector2(0.78f, 0.62f));   // cabin
        body.Box(new Vector3(0, 0.62f, -0.25f) * S, new Vector3(1.2f, 0.05f, 1.25f) * S, red);                    // roof
        body.Box(new Vector3(0, 0.03f, 1.55f) * S, new Vector3(1.7f, 0.16f, 1.1f) * S, red, new Vector2(0.98f, 0.9f)); // bonnet
        body.Box(new Vector3(0.62f, -0.12f, 2.13f) * S, new Vector3(0.36f, 0.14f, 0.06f) * S, lamp);              // headlights
        body.Box(new Vector3(-0.62f, -0.12f, 2.13f) * S, new Vector3(0.36f, 0.14f, 0.06f) * S, lamp);
        body.Box(new Vector3(0.65f, -0.1f, -2.13f) * S, new Vector3(0.4f, 0.13f, 0.06f) * S, tail);               // tail lights
        body.Box(new Vector3(-0.65f, -0.1f, -2.13f) * S, new Vector3(0.4f, 0.13f, 0.06f) * S, tail);
        body.Box(new Vector3(0, 0.36f, -1.95f) * S, new Vector3(1.5f, 0.05f, 0.42f) * S, dark);                   // spoiler
        body.Box(new Vector3(0.7f, 0.22f, -1.85f) * S, new Vector3(0.05f, 0.24f, 0.08f) * S, dark);
        body.Box(new Vector3(-0.7f, 0.22f, -1.85f) * S, new Vector3(0.05f, 0.24f, 0.08f) * S, dark);
        Attach(car, "body", body.ToMesh("carBody"), mat);

        var wheels = new Transform[4];
        for (int i = 0; i < 4; i++)
        {
            var wb = new MeshBuilder();
            wb.CylinderX(Vector3.zero, 0.33f * S, 0.24f * S, 10, tyre);
            wb.CylinderX(new Vector3((i % 2 == 0 ? -0.121f : 0.121f) * S, 0, 0), 0.2f * S, 0.02f, 5, rim);
            var go = Attach(car, "wheel" + i, wb.ToMesh("wheel"), mat);
            wheels[i] = go.transform;
        }
        return wheels;
    }

    static GameObject Attach(Transform parent, string name, Mesh m, Material mat)
    {
        var go = new GameObject(name); go.transform.SetParent(parent, false);
        go.AddComponent<MeshFilter>().sharedMesh = m;
        var mr = go.AddComponent<MeshRenderer>(); mr.sharedMaterial = mat;
        mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        return go;
    }
}
