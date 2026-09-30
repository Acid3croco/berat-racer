using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;
using UnityEngine.InputSystem;
using K = NetProtocol.Kind;

/// <summary>
/// Online play over UDP (NetProtocol), no server needed: one player opens a session (host) and gives the others its ip:port and the password.
/// Every player drives his own car with his own physics and assists and streams its state; the host relays them and owns the traffic, which the others mirror.
/// O opens the panel; -host [port] / -join ip:port with -password and -name do the same from the command line.
/// Same code on macOS and Windows: plain .NET sockets, a background thread only receives, everything else runs on the main thread.
/// </summary>
public class NetSession : MonoBehaviour
{
    public enum Role { Off, Host, Client }
    public static bool Live => current != null && current.role != Role.Off;
    public static bool Typing => current != null && current.panelOpen;          // the panel has the keyboard: no driving, no hotkeys
    static NetSession current;

    const float StateHz = 30f, TrafficHz = 20f, TrafficRange = 1300f, Timeout = 6f, JoinGiveUp = 15f;

    Role role; string status = "offline";
    Socket sock; Thread rx; volatile bool running;
    readonly ConcurrentQueue<(IPEndPoint from, byte[] data)> inbox = new ConcurrentQueue<(IPEndPoint, byte[])>();
    readonly PacketWriter w = new PacketWriter();
    double stateNext, trafficNext, lastSendError;

    // the local player
    CarController car; int carIndex; byte respawns; Traffic traffic; MiniMap minimap; Material mat;
    string playerName, password = "", portText, address;

    // host
    class Peer { public IPEndPoint ep; public byte id; public string name = ""; public byte[] challenge; public bool authed; public double heard; public Vector3 pos; }
    readonly Dictionary<IPEndPoint, Peer> peers = new Dictionary<IPEndPoint, Peer>();
    int port;

    // client
    IPEndPoint server; byte myId; bool welcomed; double joinedAt, helloNext, serverHeard;

    // everyone else's car, by player id
    readonly Dictionary<byte, RemoteCar> cars = new Dictionary<byte, RemoteCar>();
    readonly List<TrafficSnap> trafficOut = new List<TrafficSnap>();

    void Awake()
    {
        current = this;
        playerName = PlayerPrefs.GetString("net.name", System.Environment.UserName);
        portText = PlayerPrefs.GetString("net.port", NetProtocol.DefaultPort.ToString());
        address = PlayerPrefs.GetString("net.address", "");
    }

    /// <summary>GameBootstrap: the player's car changed (spawn, F). The others rebuild it with the right model.</summary>
    public void SetCar(CarController c, int index, Traffic t, MiniMap m)
    {
        car = c; carIndex = index; traffic = t; minimap = m; respawns++;
        c.Respawned += () => respawns++;
        if (traffic != null) { traffic.SetReplica(role == Role.Client && welcomed); SyncOthers(); }
    }

    public void StartFromCommandLine()
    {
        var a = System.Environment.GetCommandLineArgs();
        string Arg(string key) { int i = System.Array.IndexOf(a, key); return i >= 0 && i + 1 < a.Length && !a[i + 1].StartsWith("-") ? a[i + 1] : null; }
        if (Arg("-name") != null) playerName = Arg("-name");
        if (Arg("-password") != null) password = Arg("-password");
        if (System.Array.IndexOf(a, "-host") >= 0) { if (Arg("-host") != null) portText = Arg("-host"); Host(); }
        else if (Arg("-join") != null) { address = Arg("-join"); Join(); }
    }

    // ------------------------------------------------------------------ open / join / leave
    void Host()
    {
        if (!Ready()) return;
        if (!int.TryParse(portText, out port) || port < 1 || port > 65535) { status = "port must be 1-65535"; return; }
        if (!OpenSocket(new IPEndPoint(IPAddress.Any, port))) return;
        role = Role.Host;
        status = $"session open on UDP port {port}";
        Log.I("net", $"hosting on UDP {port} as '{playerName}', LAN address(es): {string.Join(", ", LanAddresses())}");
    }

    void Join()
    {
        if (!Ready()) return;
        if (!ParseAddress(address, out server, out string err)) { status = err; return; }
        if (!OpenSocket(new IPEndPoint(IPAddress.Any, 0))) return;
        role = Role.Client; welcomed = false; joinedAt = Now; helloNext = 0; serverHeard = Now;
        status = $"calling {server}…";
        Log.I("net", $"joining {server} as '{playerName}'");
    }

    bool Ready()
    {
        if (role != Role.Off) Leave("restart");
        playerName = Clean(playerName);
        if (password.Length == 0) { status = "choose a password first"; return false; }
        if (car == null) { status = "wait for the world to load"; return false; }
        PlayerPrefs.SetString("net.name", playerName); PlayerPrefs.SetString("net.port", portText); PlayerPrefs.SetString("net.address", address); PlayerPrefs.Save();
        return true;
    }

    public void Leave(string why)
    {
        if (role == Role.Off) return;
        Log.I("net", $"leaving the session ({why})");
        w.Begin(K.Bye).U8(role == Role.Host ? NetProtocol.HostId : myId);          // courtesy: the others don't wait for the time-out
        if (role == Role.Host) foreach (var p in peers.Values) if (p.authed) Send(p.ep);
        if (role == Role.Client && welcomed) Send(server);
        running = false;
        try { sock?.Close(); } catch (System.Exception) { }
        sock = null; rx = null;
        peers.Clear(); foreach (var id in cars.Keys.ToList()) RemoveCar(id);
        role = Role.Off; welcomed = false;
        if (traffic != null) traffic.SetReplica(false);
        status = why;
    }

    void OnApplicationQuit() => Leave("quit");
    void OnDestroy() { Leave("quit"); if (current == this) current = null; }

    bool OpenSocket(IPEndPoint bind)
    {
        try
        {
            sock = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp) { ReceiveBufferSize = 1 << 20, SendBufferSize = 1 << 20 };
            // Windows reports an ICMP "port unreachable" (a player who just quit) as a ConnectionReset on the NEXT receive; switch that off (SIO_UDP_CONNRESET)
            if (Application.platform == RuntimePlatform.WindowsPlayer || Application.platform == RuntimePlatform.WindowsEditor)
                sock.IOControl(unchecked((int)0x9800000C), new byte[4], null);
            sock.Bind(bind);
        }
        catch (SocketException e) { status = $"cannot open UDP {bind.Port}: {e.Message}"; Log.I("net", status); sock?.Close(); sock = null; return false; }
        running = true;
        rx = new Thread(ReceiveLoop) { IsBackground = true, Name = "net receive" }; rx.Start();
        return true;
    }

    /// <summary>Background thread: datagrams into the inbox, nothing else (Unity's API belongs to the main thread).</summary>
    void ReceiveLoop()
    {
        var s = sock; var buf = new byte[2048];
        while (running)
        {
            try
            {
                EndPoint from = new IPEndPoint(IPAddress.Any, 0);
                int n = s.ReceiveFrom(buf, ref from);
                if (n <= 0 || inbox.Count > 4096) continue;                             // flooded: drop
                var data = new byte[n]; System.Buffer.BlockCopy(buf, 0, data, 0, n);
                inbox.Enqueue(((IPEndPoint)from, data));
            }
            catch (SocketException) { if (!running) return; }                         // ConnectionReset and friends: keep listening
            catch (System.ObjectDisposedException) { return; }
        }
    }

    // ------------------------------------------------------------------ frame
    static double Now => Time.realtimeSinceStartupAsDouble;

    void Update()
    {
        HandlePanelKeys();
        if (role == Role.Off) return;
        for (int n = 0; n < 4096 && inbox.TryDequeue(out var m); n++)
        {
            try { var r = new PacketReader(m.data); if (r.Valid) { if (role == Role.Host) HostReceive(m.from, r); else ClientReceive(m.from, r); } }
            catch (System.FormatException) { }                                        // truncated or garbage datagram
        }
        if (role == Role.Off) return;                                                 // a Reject or Bye ended it
        double now = Now;
        if (role == Role.Host) HostTick(now); else ClientTick(now);
        if (now >= stateNext)
        {
            stateNext = System.Math.Max(stateNext, now - 0.1) + 1.0 / StateHz;
            if (car != null && (role == Role.Host || welcomed)) SendOwnState(now);
            foreach (var kv in cars.Where(kv => kv.Value == null || now - kv.Value.LastHeard > Timeout).ToList()) RemoveCar(kv.Key);   // silent for too long
        }
        if (now >= logNext) { logNext = now + 5.0; LogState(now); }
    }

    double logNext;
    void LogState(double now)
    {
        var sb = new System.Text.StringBuilder($"{role} {status}; traffic {(traffic == null ? "none" : traffic.Replica ? $"mirrored {traffic.Count}" : $"simulated {traffic.Count}")}");
        foreach (var rc in cars.Values)
            if (rc != null) sb.Append($"; car {rc.Id} '{rc.Name}' {rc.Spec.Name} at ({rc.transform.position.x:F1}, {rc.transform.position.y:F1}, {rc.transform.position.z:F1}) {rc.Velocity.magnitude * 3.6f:F0} km/h, heard {(now - rc.LastHeard) * 1000:F0} ms ago");
        Log.I("net", sb.ToString());
    }

    void SendOwnState(double now)
    {
        var s = RemoteCar.Capture(car, carIndex, playerName, respawns, now);
        s.Id = role == Role.Host ? NetProtocol.HostId : myId;
        w.Begin(K.State); s.Write(w);
        if (role == Role.Host) { foreach (var p in peers.Values) if (p.authed) Send(p.ep); }
        else Send(server);
    }

    // ------------------------------------------------------------------ host
    void HostReceive(IPEndPoint from, PacketReader r)
    {
        peers.TryGetValue(from, out var p);
        switch (r.Kind)
        {
            case K.Hello:
                {
                    string name = Clean(r.Str());
                    if (p != null && p.authed) { SendWelcome(p); return; }                  // our Welcome was lost
                    if (p == null)
                    {
                        if (peers.Values.Count(q => q.authed) >= NetProtocol.MaxPlayers - 1) { w.Begin(K.Reject).Str("the session is full"); Send(from); return; }
                        if (peers.Count > 64) return;                                     // too many strangers half-way through the handshake
                        p = new Peer { ep = from, challenge = NetProtocol.NewChallenge() };
                        peers[from] = p;
                    }
                    p.name = name; p.heard = Now;
                    w.Begin(K.Challenge).Bytes(p.challenge); Send(from);
                    return;
                }
            case K.Auth:
                {
                    if (p == null || p.authed) return;
                    if (!NetProtocol.SameMac(r.Bytes(), NetProtocol.Mac(password, p.challenge)))
                    {
                        Log.I("net", $"{from} ('{p.name}') gave a wrong password");
                        w.Begin(K.Reject).Str("wrong password"); Send(from); peers.Remove(from); return;
                    }
                    p.id = FreeId(); p.authed = true; p.heard = Now;
                    SendWelcome(p);
                    Log.I("net", $"'{p.name}' joined from {from} as player {p.id}");
                    status = $"{p.name} joined";
                    return;
                }
            case K.State:
                {
                    if (p == null || !p.authed) return;
                    var s = CarSnap.Read(r); s.Id = p.id; s.Name = p.name; p.heard = Now; p.pos = s.Pos;
                    UpsertCar(s);
                    w.Begin(K.State); s.Write(w);                                         // relay to the others
                    foreach (var q in peers.Values) if (q.authed && q != p) Send(q.ep);
                    return;
                }
            case K.Bye:
                if (p != null && p.authed) DropPeer(p, "left");
                return;
        }
    }

    void SendWelcome(Peer p) { w.Begin(K.Welcome).U8(p.id); Send(p.ep); }

    byte FreeId()
    {
        for (byte id = 1; id < 255; id++) if (!peers.Values.Any(q => q.authed && q.id == id)) return id;
        return 255;
    }

    void DropPeer(Peer p, string why)
    {
        peers.Remove(p.ep);
        if (!p.authed) return;
        Log.I("net", $"'{p.name}' (player {p.id}) {why}");
        status = $"{p.name} {why}";
        RemoveCar(p.id);
        w.Begin(K.Bye).U8(p.id);
        foreach (var q in peers.Values) if (q.authed) Send(q.ep);
    }

    void HostTick(double now)
    {
        foreach (var p in peers.Values.Where(p => now - p.heard > (p.authed ? Timeout : 10.0)).ToList()) DropPeer(p, "timed out");
        if (traffic == null || now < trafficNext) return;
        trafficNext = System.Math.Max(trafficNext, now - 0.1) + 1.0 / TrafficHz;
        foreach (var p in peers.Values)
        {
            if (!p.authed) continue;
            traffic.Capture(p.pos, TrafficRange, trafficOut);                             // each player gets the vehicles around him, in datagrams of ~1 KB
            int i = 0;
            do
            {
                w.Begin(K.Traffic).F64(now); int countAt = w.Length; w.U8(0); byte n = 0;
                for (; i < trafficOut.Count && n < 255 && w.Length + trafficOut[i].Size <= NetProtocol.MaxDatagram; i++, n++) trafficOut[i].Write(w);
                w.Buf[countAt] = n;
                Send(p.ep);
            } while (i < trafficOut.Count);
        }
    }

    // ------------------------------------------------------------------ client
    void ClientReceive(IPEndPoint from, PacketReader r)
    {
        if (!from.Equals(server)) return;                                                 // only the host talks to us
        serverHeard = Now;
        switch (r.Kind)
        {
            case K.Challenge:
                w.Begin(K.Auth).Bytes(NetProtocol.Mac(password, r.Bytes())); Send(server);
                return;
            case K.Welcome:
                if (welcomed) return;
                myId = r.U8(); welcomed = true;
                status = $"connected to {server} as player {myId}";
                Log.I("net", status);
                if (traffic != null) traffic.SetReplica(true);
                return;
            case K.Reject:
                { string why = r.Str(); Log.I("net", "host refused: " + why); Leave("refused: " + why); return; }
            case K.State:
                { var s = CarSnap.Read(r); if (welcomed && s.Id != myId) UpsertCar(s); return; }
            case K.Traffic:
                {
                    if (!welcomed || traffic == null) return;
                    double t = r.F64(); int n = r.U8(); double now = Now;
                    for (int i = 0; i < n; i++) traffic.Mirror(TrafficSnap.Read(r), t, now);
                    return;
                }
            case K.Bye:
                {
                    byte id = r.U8();
                    if (id == NetProtocol.HostId) Leave("the host closed the session"); else RemoveCar(id);
                    return;
                }
        }
    }

    void ClientTick(double now)
    {
        if (!welcomed)
        {
            if (now - joinedAt > JoinGiveUp) { Leave($"no answer from {server} (address, port, firewall?)"); return; }
            if (now >= helloNext) { helloNext = now + 1.0; w.Begin(K.Hello).Str(playerName); Send(server); }   // UDP may drop it: say it again
        }
        else if (now - serverHeard > Timeout) Leave("lost the connection to the host");
    }

    // ------------------------------------------------------------------ remote cars
    void UpsertCar(in CarSnap s)
    {
        if (cars.TryGetValue(s.Id, out var rc) && rc != null && rc.CarIndex != s.CarIndex) { RemoveCar(s.Id); rc = null; }    // he switched car: build the new model
        if (rc == null)
        {
            if (mat == null) mat = new Material(GameAssets.Flat);
            rc = RemoteCar.Create(s, mat); cars[s.Id] = rc; SyncOthers();
        }
        rc.Push(s, Now);
    }

    void RemoveCar(byte id)
    {
        if (!cars.TryGetValue(id, out var rc)) return;
        cars.Remove(id);
        if (rc != null) { Log.I("net", $"remote car {id} '{rc.Name}' removed"); Destroy(rc.gameObject); }
        SyncOthers();
    }

    void SyncOthers()
    {
        if (traffic == null) return;
        traffic.Others.Clear(); traffic.Others.AddRange(cars.Values.Where(c => c != null));
    }

    // ------------------------------------------------------------------ helpers
    void Send(IPEndPoint to)
    {
        try { sock?.SendTo(w.Buf, 0, w.Length, SocketFlags.None, to); }
        catch (System.Exception e) when (e is SocketException || e is System.ObjectDisposedException)
        {
            if (Now - lastSendError > 5.0) { lastSendError = Now; Log.I("net", $"send to {to} failed: {e.Message}"); }
        }
    }

    static string Clean(string name)
    {
        var s = new string((name ?? "").Where(ch => !char.IsControl(ch)).ToArray()).Trim();
        if (s.Length > 16) s = s.Substring(0, 16);
        return s.Length > 0 ? s : "driver";
    }

    /// <summary>"ip:port", "host.name:port" or just "ip" (default port).</summary>
    static bool ParseAddress(string text, out IPEndPoint ep, out string error)
    {
        ep = null; error = null; text = (text ?? "").Trim();
        if (text.Length == 0) { error = "enter the host's address (ip:port)"; return false; }
        int port = NetProtocol.DefaultPort; string host = text;
        int colon = text.LastIndexOf(':');
        if (colon > 0) { host = text.Substring(0, colon); if (!int.TryParse(text.Substring(colon + 1), out port) || port < 1 || port > 65535) { error = "bad port in " + text; return false; } }
        try
        {
            var ip = IPAddress.TryParse(host, out var parsed) ? parsed : Dns.GetHostAddresses(host).FirstOrDefault(a => a.AddressFamily == AddressFamily.InterNetwork);
            if (ip == null || ip.AddressFamily != AddressFamily.InterNetwork) { error = $"no IPv4 address for '{host}'"; return false; }
            ep = new IPEndPoint(ip, port); return true;
        }
        catch (SocketException e) { error = $"cannot resolve '{host}': {e.Message}"; return false; }
    }

    static List<string> LanAddresses()
    {
        var list = new List<string>();
        try
        {
            foreach (var ni in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (ni.OperationalStatus != OperationalStatus.Up || ni.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                foreach (var ua in ni.GetIPProperties().UnicastAddresses)
                    if (ua.Address.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(ua.Address)) list.Add(ua.Address.ToString());
            }
        }
        catch (System.Exception e) { Log.I("net", "cannot list network interfaces: " + e.Message); }
        return list.Distinct().ToList();
    }

    // ------------------------------------------------------------------ panel and HUD
    bool panelOpen; GUIStyle title, label, tag, hud; List<string> lan;

    void HandlePanelKeys()
    {
        var kb = Keyboard.current; if (kb == null) return;
        if (!panelOpen && kb.oKey.wasPressedThisFrame) { panelOpen = true; lan = LanAddresses(); }
        else if (panelOpen && kb.escapeKey.wasPressedThisFrame) panelOpen = false;
    }

    void Styles()
    {
        if (title != null) return;
        title = new GUIStyle(GUI.skin.label) { fontSize = 24, fontStyle = FontStyle.Bold }; title.normal.textColor = Color.white;
        label = new GUIStyle(GUI.skin.label) { fontSize = 15, wordWrap = true }; label.normal.textColor = Color.white;
        tag = new GUIStyle(GUI.skin.label) { fontSize = 14, fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleCenter }; tag.normal.textColor = new Color(1f, 0.9f, 0.4f);
        hud = new GUIStyle(GUI.skin.label) { fontSize = 15, alignment = TextAnchor.UpperRight };                       // under the position box hud.normal.textColor = new Color(0.6f, 1f, 0.7f);
    }

    void OnGUI()
    {
        Styles();
        GUI.depth = -10;                                                                  // over the game's HUD
        if (role != Role.Off)
        {
            DrawTags();
            string who = string.Join(", ", cars.Values.Where(c => c != null).Select(c => c.Name));
            GUI.Label(new Rect(Screen.width - 914, 120, 900, 22), $"ONLINE ({(role == Role.Host ? "host" : "client")}) — {status}{(who.Length > 0 ? "   |   with " + who : "")}   [O]", hud);
        }
        if (panelOpen) DrawPanel();
    }

    /// <summary>Name and distance over every other player's car, and his dot on the minimap.</summary>
    void DrawTags()
    {
        var cam = Camera.main; if (cam == null || Event.current.type != EventType.Repaint) return;
        foreach (var rc in cars.Values)
        {
            if (rc == null) continue;
            Vector3 p = rc.transform.position + Vector3.up * 1.9f;
            Vector3 sp = cam.WorldToScreenPoint(p);
            if (sp.z > 0)
            {
                float d = car != null ? Vector3.Distance(car.transform.position, rc.transform.position) : 0f;
                GUI.Label(new Rect(sp.x - 120, Screen.height - sp.y - 22, 240, 22), $"{rc.Name}  {(d < 1000 ? $"{d:F0} m" : $"{d / 1000:F1} km")}", tag);
            }
            if (minimap != null && minimap.ToGui(rc.transform.position, out Vector2 g))
            {
                GUI.color = new Color(1f, 0.85f, 0.2f); GUI.DrawTexture(new Rect(g.x - 5, g.y - 5, 10, 10), Texture2D.whiteTexture); GUI.color = Color.white;
            }
        }
    }

    void DrawPanel()
    {
        float wdt = 560, hgt = role == Role.Off ? 420 : 330;
        var r = new Rect((Screen.width - wdt) / 2, (Screen.height - hgt) / 2, wdt, hgt);
        GUI.color = new Color(0, 0, 0, 0.82f); GUI.DrawTexture(r, Texture2D.whiteTexture); GUI.color = Color.white;
        GUILayout.BeginArea(new Rect(r.x + 18, r.y + 12, r.width - 36, r.height - 24));
        GUILayout.Label("ONLINE", title);
        if (role == Role.Off)
        {
            Row("Your name", ref playerName);
            Row("Password", ref password, true);
            GUILayout.Space(10);
            GUILayout.Label("Open a session — you host it and your traffic is shared with everyone:", label);
            GUILayout.BeginHorizontal(); GUILayout.Label("UDP port", label, GUILayout.Width(110)); portText = GUILayout.TextField(portText, 5, GUILayout.Width(80));
            if (GUILayout.Button("Open session", GUILayout.Width(140))) Host();
            GUILayout.EndHorizontal();
            GUILayout.Space(10);
            GUILayout.Label("Join a friend's session:", label);
            GUILayout.BeginHorizontal(); GUILayout.Label("Address", label, GUILayout.Width(110)); address = GUILayout.TextField(address, 64, GUILayout.Width(220));
            if (GUILayout.Button("Join", GUILayout.Width(80))) Join();
            GUILayout.EndHorizontal();
            GUILayout.Space(10);
            GUILayout.Label("Everyone needs the same map: positions are shared as world coordinates.", label);
        }
        else if (role == Role.Host)
        {
            GUILayout.Label($"Give your friends the password and one of these addresses:", label);
            foreach (var ip in lan ?? new List<string>()) GUILayout.Label($"   {ip}:{port}   (same network)", label);
            GUILayout.Label($"   <your public IP>:{port}   (over the internet: forward UDP port {port} on your router to this computer)", label);
            GUILayout.Space(6);
            GUILayout.Label($"Players: {playerName} (you){string.Concat(peers.Values.Where(p => p.authed).Select(p => ", " + p.name))}", label);
        }
        else GUILayout.Label($"Session at {server}{(welcomed ? $", you are player {myId}" : "")}", label);
        GUILayout.Label(status, label);
        GUILayout.FlexibleSpace();
        GUILayout.BeginHorizontal();
        if (role != Role.Off && GUILayout.Button(role == Role.Host ? "Close session" : "Leave", GUILayout.Width(140))) Leave("left the session");
        GUILayout.FlexibleSpace();
        if (GUILayout.Button("Back to the game (Esc)", GUILayout.Width(200))) panelOpen = false;
        GUILayout.EndHorizontal();
        GUILayout.EndArea();
    }

    void Row(string name, ref string value, bool secret = false)
    {
        GUILayout.BeginHorizontal(); GUILayout.Label(name, label, GUILayout.Width(110));
        value = secret ? GUILayout.PasswordField(value, '*', 32, GUILayout.Width(220)) : GUILayout.TextField(value, 16, GUILayout.Width(220));
        GUILayout.EndHorizontal();
    }
}
