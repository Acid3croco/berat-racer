using System.Globalization;
using System.Text.RegularExpressions;
using UnityEngine;

/// <summary>
/// Coordinate system for bug reports. Origin = centre of Berat, x east, z north (metres). The world is divided into 5 x 5 m cells:
/// cell (gx, gz) covers x in [5 gx, 5 gx + 5) and z in [5 gz, 5 gz + 5). Lambert-93 (EPSG:2154) is shown too so a spot can be found on any French map.
/// </summary>
public static class Grid
{
    public const float CellSize = 5f;
    public const double OriginE = 551972, OriginN = 6254819;

    public static Vector2Int CellOf(float x, float z) => new Vector2Int(Mathf.FloorToInt(x / CellSize), Mathf.FloorToInt(z / CellSize));
    public static string CellLabel(float x, float z) { var c = CellOf(x, z); return $"({c.x}, {c.y})"; }
    public static string Lambert(float x, float z) => $"E {OriginE + x:F0}  N {OriginN + z:F0}";

    /// <summary>One line that fully describes a spot, to paste into a bug report or back into the game (J).</summary>
    public static string Report(Vector3 p, float heading, string car, string surface)
        => $"BERAT x={p.x:F1} z={p.z:F1} y={p.y:F1} cell={CellLabel(p.x, p.z)} heading={heading:F0} surface={surface} car={car} L93={Lambert(p.x, p.z)}";

    static readonly Regex Cell = new Regex(@"cell\s*[=:]?\s*\(?\s*(-?\d+)\s*[, ]\s*(-?\d+)\s*\)?", RegexOptions.IgnoreCase);
    static readonly Regex Xz = new Regex(@"x\s*[=:]?\s*(-?\d+(?:[.,]\d+)?)\D+?z\s*[=:]?\s*(-?\d+(?:[.,]\d+)?)", RegexOptions.IgnoreCase);
    static readonly Regex Pair = new Regex(@"^\s*\(?\s*(-?\d+(?:[.,]\d+)?)\s*[,; ]\s*(-?\d+(?:[.,]\d+)?)\s*\)?\s*$");

    /// <summary>Accepts "x=3 z=756", "x3 z756", "cell (0, 151)" (its centre), a BERAT report line, or a bare "3 756".</summary>
    public static bool TryParse(string text, out float x, out float z)
    {
        x = z = 0;
        if (string.IsNullOrWhiteSpace(text)) return false;
        float F(string s) => float.Parse(s.Replace(',', '.'), CultureInfo.InvariantCulture);
        var m = Xz.Match(text);
        if (m.Success) { x = F(m.Groups[1].Value); z = F(m.Groups[2].Value); return true; }
        m = Cell.Match(text);
        if (m.Success) { x = (int.Parse(m.Groups[1].Value) + 0.5f) * CellSize; z = (int.Parse(m.Groups[2].Value) + 0.5f) * CellSize; return true; }
        m = Pair.Match(text);
        if (m.Success) { x = F(m.Groups[1].Value); z = F(m.Groups[2].Value); return true; }
        return false;
    }
}
