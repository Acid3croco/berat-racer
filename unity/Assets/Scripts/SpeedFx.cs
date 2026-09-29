using UnityEngine;

/// <summary>Screen-space speed effects on the main camera: radial blur + vignette above ~90 km/h.</summary>
[RequireComponent(typeof(Camera))]
public class SpeedFx : MonoBehaviour
{
    public CarController Car;
    public float StartKmh = 90f, FullKmh = 260f, MaxBlur = 1.0f, MaxVignette = 0.45f;
    public float Strength { get; private set; }
    Material mat;

    void OnEnable()
    {
        var sh = Resources.Load<Shader>("SpeedBlur");
        if (sh == null || !sh.isSupported) { Log.I("fx", "SpeedBlur shader missing/unsupported: effect disabled"); enabled = false; return; }
        mat = new Material(sh);
    }

    void Update()
    {
        float target = (Car == null || Time.timeScale == 0f) ? 0f : Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(StartKmh, FullKmh, Car.SpeedKmh));
        Strength = Mathf.Lerp(Strength, target, 1f - Mathf.Exp(-4f * Time.unscaledDeltaTime));
    }

    void OnRenderImage(RenderTexture src, RenderTexture dst)
    {
        if (mat == null || Strength < 0.01f) { Graphics.Blit(src, dst); return; }
        mat.SetFloat("_Strength", Strength * MaxBlur); mat.SetFloat("_Vig", Strength * MaxVignette);
        Graphics.Blit(src, dst, mat);
    }
}
