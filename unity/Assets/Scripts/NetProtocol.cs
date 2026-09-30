using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

/// <summary>
/// Wire format of an online session (UDP, one datagram = one message). Every message starts with "BR", the protocol version and its kind.
///
/// Joining: client Hello -> host Challenge (16 random bytes) -> client Auth (HMAC-SHA256 of the challenge, keyed with the password) -> host Welcome (player id) or Reject.
/// The password itself never travels. Then everyone streams its own car (State, 30 Hz); the host relays each car to the other players and streams the traffic (20 Hz).
/// Bye says a player left (id 0 = the host closed the session). Both sides assume the same map: positions are sent as plain world coordinates.
/// </summary>
public static class NetProtocol
{
    public const byte Version = 2;                     // 2: engine sound (rpm, load, slip) in State
    public const int DefaultPort = 27960, MaxPlayers = 8, MaxDatagram = 1200;
    public const byte HostId = 0;

    public enum Kind : byte { Hello = 1, Challenge = 2, Auth = 3, Welcome = 4, Reject = 5, State = 10, Traffic = 11, Bye = 12 }

    public static byte[] Mac(string password, byte[] challenge)
    {
        using (var h = new HMACSHA256(Encoding.UTF8.GetBytes(password))) return h.ComputeHash(challenge);
    }

    public static bool SameMac(byte[] a, byte[] b)
    {
        if (a == null || b == null || a.Length != b.Length) return false;
        int diff = 0; for (int i = 0; i < a.Length; i++) diff |= a[i] ^ b[i];         // constant time: no early exit
        return diff == 0;
    }

    public static byte[] NewChallenge() { var c = new byte[16]; using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(c); return c; }
}

/// <summary>Little-endian writer into a reusable buffer.</summary>
public class PacketWriter
{
    public readonly byte[] Buf = new byte[NetProtocol.MaxDatagram + 256];
    public int Length { get; private set; }

    public PacketWriter Begin(NetProtocol.Kind kind) { Length = 0; U8((byte)'B'); U8((byte)'R'); U8(NetProtocol.Version); U8((byte)kind); return this; }
    public void U8(byte v) { Buf[Length++] = v; }
    public void U16(ushort v) { Buf[Length++] = (byte)v; Buf[Length++] = (byte)(v >> 8); }
    public void I32(int v) { for (int k = 0; k < 4; k++) Buf[Length++] = (byte)(v >> (8 * k)); }
    public void I64(long v) { for (int k = 0; k < 8; k++) Buf[Length++] = (byte)(v >> (8 * k)); }
    public void F32(float v) => I32(BitConverter.SingleToInt32Bits(v));
    public void F64(double v) => I64(BitConverter.DoubleToInt64Bits(v));
    public void V3(Vector3 v) { F32(v.x); F32(v.y); F32(v.z); }
    public void Q(Quaternion q) { F32(q.x); F32(q.y); F32(q.z); F32(q.w); }
    public void Bytes(byte[] b) { U8((byte)b.Length); Array.Copy(b, 0, Buf, Length, b.Length); Length += b.Length; }
    public void Str(string s) { var b = Encoding.UTF8.GetBytes(s ?? ""); if (b.Length > 64) Array.Resize(ref b, 64); Bytes(b); }
}

/// <summary>Reader over a received datagram. Reading past the end throws, and the caller drops the message.</summary>
public class PacketReader
{
    readonly byte[] buf; int pos;
    public NetProtocol.Kind Kind { get; }
    public bool Valid { get; }
    public int Remaining => buf.Length - pos;

    public PacketReader(byte[] data)
    {
        buf = data;
        Valid = data.Length >= 4 && data[0] == 'B' && data[1] == 'R' && data[2] == NetProtocol.Version;
        if (Valid) { Kind = (NetProtocol.Kind)data[3]; pos = 4; }
    }

    void Need(int n) { if (pos + n > buf.Length) throw new FormatException("truncated message"); }
    public byte U8() { Need(1); return buf[pos++]; }
    public ushort U16() { Need(2); ushort v = (ushort)(buf[pos] | buf[pos + 1] << 8); pos += 2; return v; }
    public int I32() { Need(4); int v = buf[pos] | buf[pos + 1] << 8 | buf[pos + 2] << 16 | buf[pos + 3] << 24; pos += 4; return v; }
    public long I64() { long lo = (uint)I32(), hi = (uint)I32(); return lo | hi << 32; }
    public float F32() => BitConverter.Int32BitsToSingle(I32());
    public double F64() => BitConverter.Int64BitsToDouble(I64());
    public Vector3 V3() => new Vector3(F32(), F32(), F32());
    public Quaternion Q() => new Quaternion(F32(), F32(), F32(), F32());
    public byte[] Bytes() { int n = U8(); Need(n); var b = new byte[n]; Array.Copy(buf, pos, b, 0, n); pos += n; return b; }
    public string Str() => Encoding.UTF8.GetString(Bytes());
}

/// <summary>One player's car at one instant: what the other players need to draw it, spin its wheels and leave its smoke and skid marks.</summary>
public struct CarSnap
{
    public byte Id, CarIndex, Respawns, Lights;         // Lights: 1 = brake, 2 = reverse
    public string Name;
    public double T;                                     // sender's clock, s
    public Vector3 Pos, Vel; public Quaternion Rot;
    public float SteerL, SteerR;                         // front wheel angles, rad
    public float Rpm, Load, Slip;                        // engine speed, engine load 0..1, tyre slip 0..1: the sound of the car
    public Surface Surface;
    public Wheels4 Drop, Omega, Fx;                      // suspension drop below the mount (m), wheel spin rate (rad/s), smoke / skid intensity 0..1
    public byte SurfBits;                                // 2 bits per wheel: the surface under it

    public Surface WheelSurface(int i) => (Surface)((SurfBits >> (2 * i)) & 3);

    public void Write(PacketWriter w)
    {
        w.U8(Id); w.U8(CarIndex); w.U8(Respawns); w.U8(Lights); w.Str(Name); w.F64(T);
        w.V3(Pos); w.Q(Rot); w.V3(Vel); w.F32(SteerL); w.F32(SteerR); w.U8((byte)Surface); w.U8(SurfBits);
        w.F32(Rpm); w.U8(Byte01(Load)); w.U8(Byte01(Slip));
        for (int i = 0; i < 4; i++) { w.F32(Drop[i]); w.F32(Omega[i]); w.U8(Byte01(Fx[i])); }
    }

    static byte Byte01(float v) => (byte)Mathf.RoundToInt(Mathf.Clamp01(v) * 255f);

    public static CarSnap Read(PacketReader r)
    {
        var s = new CarSnap { Id = r.U8(), CarIndex = r.U8(), Respawns = r.U8(), Lights = r.U8(), Name = r.Str(), T = r.F64() };
        s.Pos = r.V3(); s.Rot = r.Q(); s.Vel = r.V3(); s.SteerL = r.F32(); s.SteerR = r.F32(); s.Surface = (Surface)r.U8(); s.SurfBits = r.U8();
        s.Rpm = r.F32(); s.Load = r.U8() / 255f; s.Slip = r.U8() / 255f;
        for (int i = 0; i < 4; i++) { s.Drop[i] = r.F32(); s.Omega[i] = r.F32(); s.Fx[i] = r.U8() / 255f; }
        return s;
    }

    public static readonly Func<CarSnap, CarSnap, float, CarSnap> Blend = Lerp;          // cached: sampling must not allocate
    public static CarSnap Lerp(CarSnap a, CarSnap b, float k)
    {
        var s = b;                                        // discrete fields (surfaces, lights, name) come from the newer one
        s.T = a.T + (b.T - a.T) * k;
        s.Pos = Vector3.LerpUnclamped(a.Pos, b.Pos, k); s.Rot = Quaternion.Slerp(a.Rot, b.Rot, k); s.Vel = Vector3.Lerp(a.Vel, b.Vel, k);
        s.SteerL = Mathf.Lerp(a.SteerL, b.SteerL, k); s.SteerR = Mathf.Lerp(a.SteerR, b.SteerR, k);
        s.Rpm = Mathf.Lerp(a.Rpm, b.Rpm, k); s.Load = Mathf.Lerp(a.Load, b.Load, k); s.Slip = Mathf.Lerp(a.Slip, b.Slip, k);
        for (int i = 0; i < 4; i++) { s.Drop[i] = Mathf.Lerp(a.Drop[i], b.Drop[i], k); s.Omega[i] = Mathf.Lerp(a.Omega[i], b.Omega[i], k); s.Fx[i] = Mathf.Lerp(a.Fx[i], b.Fx[i], k); }
        return s;
    }
}

/// <summary>Four floats by value (a struct, so snapshots copy cleanly).</summary>
public struct Wheels4
{
    public float A, B, C, D;
    public float this[int i]
    {
        get => i == 0 ? A : i == 1 ? B : i == 2 ? C : D;
        set { if (i == 0) A = value; else if (i == 1) B = value; else if (i == 2) C = value; else D = value; }
    }
}

/// <summary>One traffic vehicle at one instant, as the host simulates it. Type and look let a client build the same vehicle the first time it sees it.</summary>
public struct TrafficSnap
{
    public ushort Id; public VehicleType Type; public byte LookA, LookB;
    public Vector3 Pos; public float Yaw, Slope, V;
    public Vector3 TrailerPos; public float TrailerYaw, TrailerPitch;     // semis only

    public void Write(PacketWriter w)
    {
        w.U16(Id); w.U8((byte)Type); w.U8(LookA); w.U8(LookB); w.V3(Pos); w.F32(Yaw); w.F32(Slope); w.F32(V);
        if (Type == VehicleType.Semi) { w.V3(TrailerPos); w.F32(TrailerYaw); w.F32(TrailerPitch); }
    }

    public static TrafficSnap Read(PacketReader r)
    {
        var s = new TrafficSnap { Id = r.U16(), Type = (VehicleType)r.U8(), LookA = r.U8(), LookB = r.U8(), Pos = r.V3(), Yaw = r.F32(), Slope = r.F32(), V = r.F32() };
        if (s.Type == VehicleType.Semi) { s.TrailerPos = r.V3(); s.TrailerYaw = r.F32(); s.TrailerPitch = r.F32(); }
        return s;
    }

    public int Size => Type == VehicleType.Semi ? 45 : 25;

    public static readonly Func<TrafficSnap, TrafficSnap, float, TrafficSnap> Blend = Lerp;          // cached: sampling must not allocate
    public static TrafficSnap Lerp(TrafficSnap a, TrafficSnap b, float k)
    {
        var s = b;
        s.Pos = Vector3.LerpUnclamped(a.Pos, b.Pos, k); s.Yaw = Mathf.LerpAngle(a.Yaw, b.Yaw, k); s.Slope = Mathf.Lerp(a.Slope, b.Slope, k); s.V = Mathf.Lerp(a.V, b.V, k);
        s.TrailerPos = Vector3.LerpUnclamped(a.TrailerPos, b.TrailerPos, k); s.TrailerYaw = Mathf.LerpAngle(a.TrailerYaw, b.TrailerYaw, k); s.TrailerPitch = Mathf.LerpAngle(a.TrailerPitch, b.TrailerPitch, k);
        return s;
    }
}

/// <summary>
/// Maps a sender's clock onto ours. offset = our time - their time, taken from the fastest recent message (the one with the least network delay);
/// it creeps up slowly so a sender whose clock runs a little slow, or a route that got longer, is followed.
/// </summary>
public class ClockSync
{
    double offset; bool known;
    public void Observe(double senderT, double localT)
    {
        double o = localT - senderT;
        if (!known || o < offset) { offset = o; known = true; }
        else offset += (o - offset) * 0.002;
    }
    public double ToSender(double localT) => localT - offset;
}

/// <summary>Snapshots of one remote thing, in the sender's time; sampled a little in the past so there are always two to blend between.</summary>
public class Timeline<T> where T : struct
{
    public const double Delay = 0.1;                    // s behind the newest data: ~3 car states, 2 traffic states
    readonly List<(double t, T s)> items = new List<(double, T)>();
    public readonly ClockSync Clock;
    public Timeline(ClockSync clock = null) { Clock = clock ?? new ClockSync(); }           // things from the same sender share its clock
    public double LastHeard { get; private set; }       // local time
    public T Newest => items[items.Count - 1].s;
    public bool Empty => items.Count == 0;

    public void Add(double senderT, T s, double localT)
    {
        Clock.Observe(senderT, localT); LastHeard = localT;
        if (items.Count > 0 && senderT <= items[items.Count - 1].t) return;            // late or duplicate datagram
        items.Add((senderT, s));
        if (items.Count > 32) items.RemoveRange(0, items.Count - 32);
    }

    public void Clear() => items.Clear();

    /// <summary>State at local time `localT` (minus the delay). Past the newest snapshot it holds the newest one; `ahead` says by how much (s).</summary>
    public T Sample(double localT, Func<T, T, float, T> lerp, out double ahead)
    {
        double t = Clock.ToSender(localT) - Delay; ahead = 0;
        if (t <= items[0].t) return items[0].s;
        for (int i = items.Count - 1; i > 0; i--)
            if (items[i - 1].t <= t)
            {
                if (t >= items[i].t) { ahead = t - items[i].t; return items[i].s; }
                float k = (float)((t - items[i - 1].t) / (items[i].t - items[i - 1].t));
                return lerp(items[i - 1].s, items[i].s, k);
            }
        return items[0].s;
    }
}
