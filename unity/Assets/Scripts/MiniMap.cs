using UnityEngine;
using UnityEngine.InputSystem;

/// <summary>
/// Heading-up minimap in the bottom-left corner: a small orthographic camera looks straight down at the car, renders into a half-float texture
/// (trees and poles are left out so the roads stay visible) and the texture is drawn through a circular tone-mapping shader. Z cycles the range.
/// </summary>
public class MiniMap : MonoBehaviour
{
    public const int HiddenLayer = 31;                          // trees and street furniture live here: the main camera sees them, the minimap does not
    static readonly float[] Ranges = { 120f, 250f, 500f, 1000f };

    WorldBuilder world; CarController car; MapView map;
    Camera cam; RenderTexture rt; Material blit; Texture2D arrow;
    int range = 1; int frame; GUIStyle north; float shownRange;

    public void Init(WorldBuilder w, CarController c, MapView m)
    {
        world = w; car = c; map = m;
        if (cam != null) return;
        rt = new RenderTexture(384, 384, 24, RenderTextureFormat.ARGBHalf) { antiAliasing = 1, name = "minimap" };
        var go = new GameObject("Minimap Camera"); go.transform.SetParent(transform, false);
        cam = go.AddComponent<Camera>();
        cam.orthographic = true; cam.enabled = false; cam.targetTexture = rt; cam.allowHDR = true; cam.allowMSAA = false;
        cam.clearFlags = CameraClearFlags.SolidColor; cam.backgroundColor = new Color(0.16f, 0.2f, 0.12f);
        cam.cullingMask = ~(1 << HiddenLayer); cam.nearClipPlane = 1f; cam.farClipPlane = 2000f; cam.useOcclusionCulling = false;
        cam.depthTextureMode = DepthTextureMode.None;
        var sh = Resources.Load<Shader>("BeratMiniMap"); blit = new Material(sh != null ? sh : Shader.Find("Hidden/InternalErrorShader"));
        arrow = new Texture2D(32, 32, TextureFormat.RGBA32, false) { filterMode = FilterMode.Bilinear };
        for (int y = 0; y < 32; y++)
            for (int x = 0; x < 32; x++)
            {   // upward-pointing arrowhead with a notch at the back
                float u = (x + 0.5f) / 32f - 0.5f, v = (y + 0.5f) / 32f;             // v: 0 = tail, 1 = tip
                float half = 0.46f * (1f - v), notch = 0.10f * (1f - v) * Mathf.Clamp01((0.35f - v) * 6f);
                bool inside = v > 0.04f && v < 0.98f && Mathf.Abs(u) < half && !(v < 0.30f && Mathf.Abs(u) < notch);
                arrow.SetPixel(x, y, inside ? new Color(1f, 0.25f, 0.15f, 1f) : Color.clear);
            }
        arrow.Apply();
    }

    void LateUpdate()
    {
        if (cam == null || world == null || !world.Ready || car == null || (map != null && map.Active)) return;
        var kb = Keyboard.current;
        if (kb != null && kb.zKey.wasPressedThisFrame && !NetSession.Typing) { range = (range + 1) % Ranges.Length; Log.I("minimap", $"range {Ranges[range]:F0} m"); }
        if ((frame++ & 1) != 0) return;
        var p = car.transform.position; float yaw = car.transform.eulerAngles.y;
        cam.orthographicSize = Ranges[range];
        cam.transform.position = new Vector3(p.x, p.y + 800f, p.z);
        cam.transform.rotation = Quaternion.Euler(90f, yaw, 0f);                 // looking down, the car's forward direction is up the screen
        cam.Render();
    }

    /// <summary>Test helper: render once now and save the tone-mapped map (what the HUD shows) as a PNG.</summary>
    public void SaveSnapshot(string path)
    {
        var p = car.transform.position; cam.orthographicSize = Ranges[range];
        cam.transform.position = new Vector3(p.x, p.y + 800f, p.z); cam.transform.rotation = Quaternion.Euler(90f, car.transform.eulerAngles.y, 0f); cam.Render();
        var tmp = RenderTexture.GetTemporary(384, 384, 0, RenderTextureFormat.ARGB32); Graphics.Blit(rt, tmp, blit);
        var tex = new Texture2D(384, 384, TextureFormat.RGBA32, false); RenderTexture.active = tmp; tex.ReadPixels(new Rect(0, 0, 384, 384), 0, 0); tex.Apply(); RenderTexture.active = null;
        System.IO.File.WriteAllBytes(path, tex.EncodeToPNG()); RenderTexture.ReleaseTemporary(tmp); Destroy(tex);
    }

    /// <summary>Screen rectangle of the map (GUI coordinates, origin top-left).</summary>
    public Rect Area
    {
        get { float s = Mathf.Clamp(Screen.height * 0.27f, 190f, 340f); return new Rect(16f, Screen.height - s - 16f, s, s); }
    }

    /// <summary>Where a world point is drawn on the minimap (GUI coordinates); points off the map are pinned to its rim. False while the minimap is hidden.</summary>
    public bool ToGui(Vector3 world, out Vector2 gui)
    {
        gui = default;
        if (cam == null || car == null || (map != null && map.Active)) return false;
        var r = Area; float yaw = car.transform.eulerAngles.y * Mathf.Deg2Rad, rad = r.width * 0.5f;
        Vector3 d = world - car.transform.position;
        float right = d.x * Mathf.Cos(yaw) - d.z * Mathf.Sin(yaw), ahead = d.x * Mathf.Sin(yaw) + d.z * Mathf.Cos(yaw);     // heading-up, like the map
        var v = new Vector2(right, -ahead) / Ranges[range] * rad;
        if (v.magnitude > rad - 6f) v = v.normalized * (rad - 6f);
        gui = r.center + v;
        return true;
    }

    public void DrawGUI()
    {
        if (cam == null || blit == null || Event.current.type != EventType.Repaint) return;
        var r = Area;
        Graphics.DrawTexture(r, rt, blit);
        float a = r.width * 0.11f;
        GUI.DrawTexture(new Rect(r.center.x - a / 2, r.center.y - a * 0.62f, a, a), arrow);
        // north marker on the rim, rotating as the car turns
        float yaw = car.transform.eulerAngles.y * Mathf.Deg2Rad, rad = r.width * 0.5f - 11f;
        Vector2 dir = new Vector2(-Mathf.Sin(yaw), -Mathf.Cos(yaw));            // where north lies on screen: (0,-1) when heading north (up), in GUI coordinates
        Vector2 np = r.center + dir * rad;
        if (north == null) { north = new GUIStyle(GUI.skin.label) { fontSize = 15, fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleCenter }; north.normal.textColor = new Color(1f, 0.92f, 0.5f); }
        GUI.Label(new Rect(np.x - 12, np.y - 12, 24, 24), "N", north);
        if (north != null) GUI.Label(new Rect(r.x, r.yMax - 22, r.width, 20), $"{Ranges[range]:F0} m   [Z]", new GUIStyle(north) { fontSize = 11, fontStyle = FontStyle.Normal });
    }
}
