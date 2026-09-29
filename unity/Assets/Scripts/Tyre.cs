using UnityEngine;

/// <summary>
/// Tyre force model: one normalised friction curve evaluated on the COMBINED slip vector, so traction and cornering share the same grip budget
/// (the friction ellipse falls out of the maths: wheelspin or lock-up eats lateral grip, hard cornering eats braking grip).
///   sx = slipRatio / KappaPeak,  sy = tan(slipAngle) / AlphaPeak,  s = |(sx, sy)|
///   F  = mu * Fz * f(s) * (sx, sy) / s        f peaks at exactly 1 for s = 1, then falls to a sliding plateau
/// </summary>
public static class Tyre
{
    public const float KappaPeak = 0.12f;                   // slip ratio of peak longitudinal force
    public static readonly float AlphaPeak = Mathf.Tan(8.5f * Mathf.Deg2Rad);   // tan of the slip angle of peak lateral force

    /// <summary>f(s): slope 2 at the origin (cornering stiffness ~ 12.6 Fz per rad), peak 1 at s = 1, smooth fall to the sliding fraction.</summary>
    public static float Curve(float s, float slide)
    {
        if (s <= 1f) return 2f * s / (1f + s * s);
        float d = (s - 1f) * 1.1f;
        return slide + (1f - slide) / (1f + d * d);
    }

    /// <summary>Force on the car from one tyre in the wheel frame (fx forward, fy right). s returns the combined slip.</summary>
    public static void Force(float kappa, float tanAlpha, float muX, float muY, float fz, float slide, out float fx, out float fy, out float s)
    {
        float sx = kappa / KappaPeak, sy = tanAlpha / AlphaPeak;
        s = Mathf.Sqrt(sx * sx + sy * sy);
        if (s < 1e-5f) { fx = fy = 0f; return; }
        float f = Curve(s, slide) / s;
        fx = muX * fz * f * sx;
        fy = -muY * fz * f * sy;               // opposes the sideways motion of the contact patch
    }

    public struct SurfaceProps { public float mu, slide, rolling; }
    public static SurfaceProps Props(Surface s)
    {
        switch (s)
        {
            case Surface.Dirt: return new SurfaceProps { mu = 0.66f, slide = 0.90f, rolling = 0.045f };
            case Surface.Grass: return new SurfaceProps { mu = 0.52f, slide = 0.88f, rolling = 0.070f };
            default: return new SurfaceProps { mu = 1.00f, slide = 0.82f, rolling = 0.013f };
        }
    }
}
