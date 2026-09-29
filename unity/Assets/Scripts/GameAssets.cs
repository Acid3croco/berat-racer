using UnityEngine;

public static class GameAssets
{
    static Shader flat, water;
    public static Shader Water
    {
        get
        {
            if (water == null) water = Resources.Load<Shader>("BeratWater");
            if (water == null) water = Shader.Find("Berat/Water");
            if (water == null) water = Flat;
            return water;
        }
    }
    /// <summary>The flat vertex-colour shader. Lives in Resources/ so player builds never strip it.</summary>
    public static Shader Flat
    {
        get
        {
            if (flat == null) flat = Resources.Load<Shader>("FlatColor");
            if (flat == null) flat = Shader.Find("Berat/FlatColor");
            if (flat == null) { Log.I("assets", "FATAL: FlatColor shader not found"); flat = Shader.Find("Hidden/InternalErrorShader"); }
            return flat;
        }
    }
}
