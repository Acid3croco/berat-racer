using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

[Serializable] public class PlaceEntry { public string n, k; public int r, pop; public float x, z; }
[Serializable] class PlaceList { public PlaceEntry[] items; }

/// <summary>Named places (cities ... hamlets) from world/places.json, with the map altitude below which each one is worth a label.</summary>
public static class Places
{
    public static PlaceEntry[] All = new PlaceEntry[0];
    static float[] maxAlt;                                   // per place: label visible while the map camera is lower than this (m)
    public static int Count => All.Length;

    public static void Load()
    {
        string path = Path.Combine(WorldData.Dir, "places.json");
        if (!File.Exists(path)) { Log.I("places", "no places.json"); return; }
        try
        {
            var list = JsonUtility.FromJson<PlaceList>("{\"items\":" + File.ReadAllText(path) + "}").items;
            var keep = new List<PlaceEntry>();
            foreach (var p in list) if (WorldData.InBounds(p.x, p.z)) keep.Add(p);
            keep.Sort((a, b) => Tier(a).CompareTo(Tier(b)) != 0 ? Tier(a).CompareTo(Tier(b)) : b.pop.CompareTo(a.pop));      // most important first: the label placer keeps the first that fits
            All = keep.ToArray(); maxAlt = new float[All.Length];
            for (int i = 0; i < All.Length; i++) maxAlt[i] = MaxAltitude(All[i]);
            Log.I("places", $"{All.Length} places inside the map");
        }
        catch (Exception e) { Log.I("places", "load failed: " + e.Message); }
    }

    /// <summary>0 = metropolis ... 6 = hamlet.</summary>
    public static int Tier(PlaceEntry p)
    {
        if (p.pop >= 100000) return 0;
        if (p.pop >= 20000) return 1;
        if (p.pop >= 5000) return 2;
        if (p.k == "town" || p.pop >= 1500) return 3;
        if (p.k == "village") return p.pop >= 500 ? 4 : 5;
        if (p.k == "suburb") return 5;
        return 6;
    }

    static float MaxAltitude(PlaceEntry p)
    {
        switch (Tier(p))
        {
            case 0: return 1e9f;
            case 1: return 90000f;
            case 2: return 45000f;
            case 3: return 22000f;
            case 4: return 12000f;
            case 5: return 6000f;
            default: return 2600f;
        }
    }
    public static float MaxAlt(int i) => maxAlt[i];

    public static int FontSize(PlaceEntry p)
    {
        switch (Tier(p)) { case 0: return 30; case 1: return 25; case 2: return 21; case 3: return 18; case 4: return 16; case 5: return 14; default: return 12; }
    }
}
