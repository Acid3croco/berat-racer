using UnityEngine;

/// <summary>
/// Screen-space finishing on the main camera (HDR in, sRGB out): bloom, ACES tone mapping, colour grade, vignette, and the speed blur
/// that fades in above ~90 km/h. Class name kept from the first version (it was just the speed blur).
/// </summary>
[RequireComponent(typeof(Camera))]
public class SpeedFx : MonoBehaviour
{
    public CarController Car;
    public float StartKmh = 90f, FullKmh = 260f, MaxBlur = 1.0f, MaxVignette = 0.45f;
    public float Exposure = 0.94f, Saturation = 1.0f, Contrast = 1.02f, BloomIntensity = 0.32f, BloomThreshold = 1.35f, BaseVignette = 0.18f;
    public float Strength { get; private set; }
    Material mat;

    void OnEnable()
    {
        var sh = Resources.Load<Shader>("BeratPostFx");
        if (sh == null || !sh.isSupported) { Log.I("fx", "PostFx shader missing/unsupported: post effects disabled"); enabled = false; return; }
        mat = new Material(sh);
        if (System.Array.IndexOf(System.Environment.GetCommandLineArgs(), "-nofxaa") >= 0) Fxaa = false;
        var cam = GetComponent<Camera>(); cam.allowHDR = true; cam.allowMSAA = true;
    }

    void Update()
    {
        float target = (Car == null || Time.timeScale == 0f) ? 0f : Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(StartKmh, FullKmh, Car.SpeedKmh));
        Strength = Mathf.Lerp(Strength, target, 1f - Mathf.Exp(-4f * Time.unscaledDeltaTime));
    }

    public bool Fxaa = true;

    void OnRenderImage(RenderTexture src, RenderTexture dst)
    {
        if (mat == null) { Graphics.Blit(src, dst); return; }
        mat.SetFloat("_Strength", Strength * MaxBlur); mat.SetFloat("_Vig", BaseVignette + Strength * MaxVignette);
        mat.SetFloat("_Exposure", Exposure); mat.SetFloat("_Saturation", Saturation); mat.SetFloat("_Contrast", Contrast);
        mat.SetFloat("_BloomIntensity", BloomIntensity); mat.SetFloat("_Threshold", BloomThreshold);
        int w = Mathf.Max(src.width / 4, 8), h = Mathf.Max(src.height / 4, 8);
        var a = RenderTexture.GetTemporary(w, h, 0, RenderTextureFormat.ARGBHalf); var b = RenderTexture.GetTemporary(w, h, 0, RenderTextureFormat.ARGBHalf);
        Graphics.Blit(src, a, mat, 0);
        Graphics.Blit(a, b, mat, 1); Graphics.Blit(b, a, mat, 2);
        Graphics.Blit(a, b, mat, 1); Graphics.Blit(b, a, mat, 2);          // two blur iterations = wide, soft glow
        mat.SetTexture("_Bloom", a);
        if (Fxaa)
        {
            var f = RenderTexture.GetTemporary(src.width, src.height, 0, RenderTextureFormat.ARGB32);
            f.filterMode = FilterMode.Bilinear;
            Graphics.Blit(src, f, mat, 3); Graphics.Blit(f, dst, mat, 4);
            RenderTexture.ReleaseTemporary(f);
        }
        else Graphics.Blit(src, dst, mat, 3);
        RenderTexture.ReleaseTemporary(a); RenderTexture.ReleaseTemporary(b);
    }
}
