using UnityEngine;
using UnityEngine.InputSystem;

/// <summary>
/// Top-down map mode (M / Select): camera rises above the car, pans and zooms over the LiDAR terrain,
/// stays inside the map borders, and teleports the car to the exact ground point under the crosshair/cursor.
/// The game is paused (timeScale 0) while the map is open.
/// </summary>
public class MapView : MonoBehaviour
{
    // zoom levels: 3 per decade on a log scale, 10 m .. 1 km  (10, 21.5, 46.4, 100, 215, 464, 1000)
    static readonly float[] Levels = { 10f, 21.5443f, 46.4159f, 100f, 215.443f, 464.159f, 1000f };
    const int StartLevel = 3;
    const float Edge = WorldData.Half - 6f;
    int level = StartLevel; float lastStep; int openedFrame = -1;
    const double OriginE = 551972, OriginN = 6254819;          // Lambert-93 of local (0,0), see tools/fetch.py

    WorldBuilder world; CarController car; FollowCamera follow; Camera cam;
    public bool Active { get; private set; }
    public float Altitude => alt; public int Level => level; public Vector2 Pos => pos; public bool AimValid => aimValid; public Vector3 Aim => aimPoint;
    public void DebugSetPos(float x, float z) { pos = new Vector2(x, z); groundSmooth = world.Data.TerrainHeight(x, z); }
    public Vector3 Focus => new Vector3(pos.x, 0, pos.y);

    Vector2 pos; float alt = 100f, groundSmooth;
    Vector3 aimPoint; bool aimValid; Vector2 aimScreen; float mouseUntil; Vector2 lastMouse;
    bool clampedX, clampedZ;
    GameObject carMarker, pin; float prevFov, prevFar, prevTimeScale = 1f; bool prevFog;
    GUIStyle label, title;

    public void Init(WorldBuilder w, CarController c, FollowCamera f, Camera camera)
    {
        world = w; car = c; follow = f; cam = camera;
        carMarker = MakeMarker("map car marker", new Color32(255, 60, 40, 255), 1f, 1.6f);
        pin = MakeMarker("map pin", new Color32(255, 220, 30, 255), 0.55f, 2.6f);
        carMarker.SetActive(false); pin.SetActive(false);
    }

    static GameObject MakeMarker(string name, Color32 col, float radius, float height)
    {
        var mb = new MeshBuilder();                                  // downward-pointing 4-sided pyramid; tip at the origin
        int tip = mb.Vertex(Vector3.zero, col);
        int[] r = new int[4];
        for (int k = 0; k < 4; k++) { float a = k * Mathf.PI / 2 + Mathf.PI / 4; r[k] = mb.Vertex(new Vector3(Mathf.Cos(a) * radius, height, Mathf.Sin(a) * radius), col); }
        for (int k = 0; k < 4; k++) mb.Tri(tip, r[(k + 1) % 4], r[k]);
        mb.Tri(r[0], r[1], r[2]); mb.Tri(r[0], r[2], r[3]);
        var go = new GameObject(name);
        go.AddComponent<MeshFilter>().sharedMesh = mb.ToMesh(name);
        var mr = go.AddComponent<MeshRenderer>(); mr.sharedMaterial = new Material(GameAssets.Flat);
        mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        return go;
    }

    public void Toggle() { if (Active) Close("toggle"); else Open(); }

    void Open()
    {
        Active = true; openedFrame = Time.frameCount;
        pos = new Vector2(car.transform.position.x, car.transform.position.z);
        level = StartLevel; alt = Levels[level]; groundSmooth = world.Data.TerrainHeight(pos.x, pos.y);
        follow.enabled = false;
        prevFov = cam.fieldOfView; prevFar = cam.farClipPlane; prevFog = RenderSettings.fog;
        cam.fieldOfView = 55f; cam.farClipPlane = 9000f; RenderSettings.fog = false;
        prevTimeScale = Time.timeScale; Time.timeScale = 0f;
        carMarker.SetActive(true); pin.SetActive(true);
        mouseUntil = 0; lastMouse = Mouse.current != null ? Mouse.current.position.ReadValue() : Vector2.zero;
        ApplyCamera();
        Log.I("map", $"OPEN at car ({pos.x:F0},{pos.y:F0}) alt {alt:F0} m");
    }

    void Close(string why)
    {
        Active = false;
        follow.enabled = true; follow.Snap();
        cam.fieldOfView = prevFov; cam.farClipPlane = prevFar; RenderSettings.fog = prevFog;
        Time.timeScale = prevTimeScale <= 0 ? 1f : prevTimeScale;
        carMarker.SetActive(false); pin.SetActive(false);
        world.HideTrees = false;
        Log.I("map", "CLOSE (" + why + ")");
    }

    public void Tick()
    {
        var kb = Keyboard.current; var mouse = Mouse.current; var gp = Gamepad.current;
        float dt = Time.unscaledDeltaTime;
        if (Time.frameCount == openedFrame) { ApplyCamera(); return; }

        // ---- pan
        Vector2 pan = Vector2.zero; int step = 0; bool fast = false, confirm = false, cancel = false;
        if (kb != null)
        {
            pan.x += (kb.dKey.isPressed || kb.rightArrowKey.isPressed ? 1 : 0) - (kb.aKey.isPressed || kb.leftArrowKey.isPressed ? 1 : 0);
            pan.y += (kb.wKey.isPressed || kb.upArrowKey.isPressed ? 1 : 0) - (kb.sKey.isPressed || kb.downArrowKey.isPressed ? 1 : 0);
            if (kb.eKey.wasPressedThisFrame || kb.equalsKey.wasPressedThisFrame || kb.numpadPlusKey.wasPressedThisFrame) step--;   // zoom in  = lower altitude
            if (kb.qKey.wasPressedThisFrame || kb.minusKey.wasPressedThisFrame || kb.numpadMinusKey.wasPressedThisFrame) step++;   // zoom out
            fast |= kb.leftShiftKey.isPressed;
            confirm |= kb.enterKey.wasPressedThisFrame || kb.spaceKey.wasPressedThisFrame;
            cancel |= kb.escapeKey.wasPressedThisFrame;
        }
        if (gp != null)
        {
            Vector2 ls = gp.leftStick.ReadValue(); if (ls.magnitude > 0.12f) pan += ls;
            if (gp.rightShoulder.wasPressedThisFrame || gp.rightTrigger.wasPressedThisFrame) step--;   // R1 / R2 = zoom in
            if (gp.leftShoulder.wasPressedThisFrame || gp.leftTrigger.wasPressedThisFrame) step++;      // L1 / L2 = zoom out
            fast |= gp.leftStickButton.isPressed;
            confirm |= gp.buttonSouth.wasPressedThisFrame;                 // Cross
            cancel |= gp.buttonEast.wasPressedThisFrame;                   // Circle
        }
        if (mouse != null)
        {
            float sc = mouse.scroll.ReadValue().y;
            if (Mathf.Abs(sc) > 0.01f && Time.unscaledTime - lastStep > 0.09f) { step += sc > 0 ? -1 : 1; lastStep = Time.unscaledTime; }   // one notch = one level
            Vector2 mp = mouse.position.ReadValue();
            if ((mp - lastMouse).sqrMagnitude > 4f) mouseUntil = Time.unscaledTime + 3f;
            lastMouse = mp;
            confirm |= mouse.leftButton.wasPressedThisFrame;
            if (mouse.leftButton.wasPressedThisFrame) mouseUntil = Time.unscaledTime + 3f;
            cancel |= mouse.rightButton.wasPressedThisFrame;
        }
        if (pan.magnitude > 1f) pan.Normalize();

        if (step != 0)
        {
            int nl = Mathf.Clamp(level + Mathf.Clamp(step, -1, 1), 0, Levels.Length - 1);
            if (nl != level) { level = nl; Log.I("map", $"zoom level {level + 1}/{Levels.Length}: {Levels[level]:F0} m"); }
        }
        alt = Mathf.Exp(Mathf.Lerp(Mathf.Log(alt), Mathf.Log(Levels[level]), 1f - Mathf.Exp(-10f * dt)));   // smooth log-space glide between levels
        world.HideTrees = alt < 45f;                                                                        // camera would sit inside the canopy
        float speed = alt * 1.1f * (fast ? 3f : 1f);
        Vector2 np = pos + pan * speed * dt;
        // stop at the map border (camera never leaves the mapped area)
        clampedX = Mathf.Abs(np.x) > Edge; clampedZ = Mathf.Abs(np.y) > Edge;
        pos = new Vector2(Mathf.Clamp(np.x, -Edge, Edge), Mathf.Clamp(np.y, -Edge, Edge));

        groundSmooth = Mathf.Lerp(groundSmooth, world.Data.TerrainHeight(pos.x, pos.y), 1f - Mathf.Exp(-6f * dt));
        ApplyCamera();

        // ---- aim: mouse cursor if it moved recently, else the screen centre (keyboard / pad)
        bool useMouse = mouse != null && Time.unscaledTime < mouseUntil;
        aimScreen = useMouse ? mouse.position.ReadValue() : new Vector2(Screen.width / 2f, Screen.height / 2f);
        aimValid = RayToGround(cam.ScreenPointToRay(aimScreen), out aimPoint);
        UpdateMarkers();

        if (Time.frameCount == openedFrame) return;                                   // the key press that opened the map must not also close it
        if (cancel || (kb != null && kb.mKey.wasPressedThisFrame)) { Close("cancel"); return; }
        if (confirm && aimValid) TeleportTo(aimPoint);
    }

    void ApplyCamera()
    {
        cam.transform.position = new Vector3(pos.x, groundSmooth + alt, pos.y);
        cam.transform.rotation = Quaternion.LookRotation(Vector3.down, Vector3.forward);   // straight down, north up
    }

    /// <summary>March the ray until it dips below the terrain, then bisect for the exact intersection.</summary>
    bool RayToGround(Ray ray, out Vector3 hit)
    {
        hit = default;
        float step = Mathf.Max(1.5f, alt * 0.02f), t = 0f, prevT = 0f;
        for (int i = 0; i < 4000; i++)
        {
            t += step;
            Vector3 p = ray.origin + ray.direction * t;
            if (Mathf.Abs(p.x) > WorldData.Half || Mathf.Abs(p.z) > WorldData.Half) return false;
            if (p.y <= world.Data.TerrainHeight(p.x, p.z))
            {
                float lo = prevT, hi = t;
                for (int k = 0; k < 24; k++)
                {
                    float mid = (lo + hi) * 0.5f; Vector3 q = ray.origin + ray.direction * mid;
                    if (q.y <= world.Data.TerrainHeight(q.x, q.z)) hi = mid; else lo = mid;
                }
                Vector3 h = ray.origin + ray.direction * hi;
                hit = new Vector3(h.x, world.Data.TerrainHeight(h.x, h.z), h.z);
                return true;
            }
            prevT = t;
        }
        return false;
    }

    void UpdateMarkers()
    {
        float s = Mathf.Max(1f, alt * 0.03f);
        Vector3 cp = car.transform.position;
        carMarker.transform.position = cp + Vector3.up * (s * 1.6f);
        carMarker.transform.localScale = Vector3.one * s;
        pin.SetActive(aimValid);
        if (aimValid) { pin.transform.position = aimPoint + Vector3.up * (s * 2.6f * 0.55f); pin.transform.localScale = Vector3.one * s * 0.55f; }
    }

    void TeleportTo(Vector3 p)
    {
        float yaw = car.transform.eulerAngles.y;
        string how = "keeping heading";
        if (world.Roads.NearestRoad(p.x, p.z, 7f, out Vector2 dir))
        {
            float a = Mathf.Atan2(dir.x, dir.y) * Mathf.Rad2Deg;                      // heading of the road (either direction)
            yaw = Mathf.Abs(Mathf.DeltaAngle(a, yaw)) <= 90f ? a : a + 180f;
            how = "aligned to road";
        }
        float ground = world.GroundHeight(p.x, p.z, p.y + 1f, out Surface surf);
        Log.I("map", $"TELEPORT to local ({p.x:F1},{p.z:F1}) elevation {ground:F2} m, surface {surf}, heading {yaw:F0}° ({how}); Lambert93 E={OriginE + p.x:F0} N={OriginN + p.z:F0}");
        car.Respawn(new Vector3(p.x, ground + 0.75f, p.z), yaw);
        Close("teleported");
    }

    public void DrawGUI()
    {
        if (label == null)
        {
            label = new GUIStyle(GUI.skin.label) { fontSize = 16 }; label.normal.textColor = Color.white;
            title = new GUIStyle(label) { fontSize = 28, fontStyle = FontStyle.Bold, alignment = TextAnchor.UpperCenter };
        }
        // crosshair
        Vector2 a = new Vector2(aimScreen.x, Screen.height - aimScreen.y);
        GUI.color = aimValid ? new Color(1f, 0.9f, 0.2f) : new Color(1f, 0.3f, 0.3f);
        GUI.DrawTexture(new Rect(a.x - 14, a.y - 1, 28, 2), Texture2D.whiteTexture);
        GUI.DrawTexture(new Rect(a.x - 1, a.y - 14, 2, 28), Texture2D.whiteTexture);
        GUI.color = Color.white;

        GUI.Label(new Rect(0, 12, Screen.width, 40), "MAP", title);
        string info = aimValid
            ? $"target  x {aimPoint.x:F0}  z {aimPoint.z:F0}   elevation {aimPoint.y:F1} m   (Lambert-93  E {OriginE + aimPoint.x:F0}  N {OriginN + aimPoint.z:F0})"
            : "target  outside the map";
        var box = new Rect(14, Screen.height - 96, Screen.width - 28, 84);
        GUI.color = new Color(0, 0, 0, 0.55f); GUI.DrawTexture(box, Texture2D.whiteTexture); GUI.color = Color.white;
        GUI.Label(new Rect(box.x + 10, box.y + 6, box.width - 20, 26), info + $"      altitude {alt:F0} m  (zoom {level + 1}/{Levels.Length})" + (clampedX || clampedZ ? "      [map edge]" : ""), label);
        GUI.Label(new Rect(box.x + 10, box.y + 32, box.width - 20, 50),
            "Pan: WASD / arrows / left stick (Shift / L3 = fast)     Zoom (10 m … 1 km, 3 steps per decade): scroll / Q,E / L1,R1 / L2,R2\nTeleport: Enter, Space, click / Cross      Close: M / Select / Esc / right-click / Circle", label);
    }
}
