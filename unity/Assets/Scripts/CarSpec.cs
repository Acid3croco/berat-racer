using UnityEngine;

public enum DriveLayout { RWD, FWD }
public enum BodyStyle { Hatch, Sedan, Sports }

/// <summary>
/// Everything that makes one car different from another: dimensions, mass distribution, suspension, engine, gearbox, brakes, aero and tyres.
/// Frame: x right, y up, z forward; the origin is the car's centre of gravity in plan, at the suspension mount plane in height
/// (the ground is 0.40 + wheel radius below it when the car is at rest).
/// </summary>
public class CarSpec
{
    public string Name, Tagline; public BodyStyle Style; public DriveLayout Layout;
    public Color32 Paint;

    // ---- geometry (m) and mass
    public float Length, Width, Height, Wheelbase, TrackF, TrackR, WheelR, TyreWidthF, TyreWidthR, FrontOverhang, RearOverhang;
    public float Mass, FrontWeight, CgHeight;                       // FrontWeight = share of weight on the front axle
    // ---- suspension
    public float RideFreqF, RideFreqR, ArbF, ArbR, DampBumpRatio = 0.27f, DampReboundRatio = 0.48f;
    // ---- tyres, brakes, aero, steering
    public float MuScale = 1f, BrakeF, BrakeR, HandbrakeTq = 3200f, CdA, ClA, ClFront = 0.4f, MaxSteerDeg = 35f;
    public float LsdPreload = 70f, MuFrontScale = 1f, MuRearScale = 1f, YawInertiaScale = 1f;
    // ---- engine and gearbox
    public float[] TqRpm, TqNm; public float Idle, Redline, EngineInertia, Efficiency; public int Cylinders;
    public float[] Ratios; public float FinalDrive, ReverseRatio;
    // ---- what the physics test expects from this car
    public float Acc0100Lo, Acc0100Hi, TopLo, TopHi, BrakeLo, BrakeHi, SkidLo, SkidHi;

    // ---- derived
    public float A => Wheelbase * (1f - FrontWeight);               // CG to front axle
    public float B => Wheelbase * FrontWeight;                      // CG to rear axle
    public float ZFront => A + FrontOverhang;
    public float ZRear => -(B + RearOverhang);
    public float GroundY => -(0.40f + WheelR);                      // ground level in the car frame at rest
    public float WheelRadiusSum => 0.40f + WheelR;
    public Vector3 InertiaTensor => new Vector3(Mass * (Height * Height + Length * Length) / 12f, YawInertiaScale * Mass * (Width * Width + Length * Length) / 12f, Mass * (Width * Width + Height * Height) / 12f);
    public Vector3 CentreOfMass => new Vector3(0f, CgHeight + GroundY, 0f);
    public Vector3 Mount(int i) { float x = (i < 2 ? TrackF : TrackR) * 0.5f * (i % 2 == 0 ? -1f : 1f); return new Vector3(x, 0f, i < 2 ? A : -B); }
    public float TyreWidth(int i) => i < 2 ? TyreWidthF : TyreWidthR;

    public static readonly CarSpec[] All = { Hatch(), Peugeot406(), Porsche992GT3() };
    public CarSpec WithPaint(Color32 paint) { var c = (CarSpec)MemberwiseClone(); c.Paint = paint; return c; }

    static CarSpec Hatch() => new CarSpec
    {
        Name = "Hot Hatch", Tagline = "1250 kg, 150 kW turbo four, RWD, 6-speed auto", Style = BodyStyle.Hatch, Layout = DriveLayout.RWD, Paint = new Color32(214, 48, 44, 255),
        Length = 4.05f, Width = 1.73f, Height = 1.42f, Wheelbase = 2.55f, TrackF = 1.50f, TrackR = 1.48f, WheelR = 0.303f, TyreWidthF = 0.215f, TyreWidthR = 0.215f, FrontOverhang = 0.78f, RearOverhang = 0.72f,
        Mass = 1250f, FrontWeight = 0.52f, CgHeight = 0.52f,
        RideFreqF = 1.34f, RideFreqR = 1.33f, ArbF = 10000f, ArbR = 9000f,
        MuScale = 1f, MuRearScale = 1.05f, YawInertiaScale = 1.10f, BrakeF = 1500f, BrakeR = 800f, CdA = 0.66f, ClA = 0.30f, ClFront = 0.4f, MaxSteerDeg = 35f,
        TqRpm = new float[] { 0, 900, 1500, 2000, 3000, 4500, 5500, 6500, 7000, 7600 }, TqNm = new float[] { 80, 120, 175, 235, 285, 292, 275, 236, 200, 120 },
        Idle = 900f, Redline = 7000f, EngineInertia = 0.16f, Efficiency = 0.92f, Cylinders = 4,
        Ratios = new float[] { 3.615f, 2.238f, 1.520f, 1.156f, 0.911f, 0.756f }, FinalDrive = 3.9f, ReverseRatio = 3.4f,
        Acc0100Lo = 6.0f, Acc0100Hi = 9.0f, TopLo = 230f, TopHi = 252f, BrakeLo = 32f, BrakeHi = 42f, SkidLo = 0.95f, SkidHi = 1.30f
    };

    static CarSpec Peugeot406() => new CarSpec
    {
        Name = "Peugeot 406 V6", Tagline = "1400 kg, 3.0 V6 (140 kW), front-wheel drive, 4-speed auto, soft suspension", Style = BodyStyle.Sedan, Layout = DriveLayout.FWD, Paint = new Color32(46, 76, 112, 255),
        Length = 4.555f, Width = 1.765f, Height = 1.39f, Wheelbase = 2.70f, TrackF = 1.51f, TrackR = 1.49f, WheelR = 0.312f, TyreWidthF = 0.205f, TyreWidthR = 0.205f, FrontOverhang = 0.93f, RearOverhang = 0.925f,
        Mass = 1400f, FrontWeight = 0.62f, CgHeight = 0.56f,
        RideFreqF = 1.15f, RideFreqR = 1.25f, ArbF = 8000f, ArbR = 4500f,
        MuScale = 0.93f, BrakeF = 1450f, BrakeR = 700f, CdA = 0.60f, ClA = 0.05f, ClFront = 0.5f, MaxSteerDeg = 34f, LsdPreload = 0f,
        TqRpm = new float[] { 0, 800, 1500, 2500, 3750, 4500, 5500, 6300, 6800 }, TqNm = new float[] { 60, 110, 170, 235, 267, 262, 250, 205, 150 },
        Idle = 800f, Redline = 6300f, EngineInertia = 0.22f, Efficiency = 0.90f, Cylinders = 6,
        Ratios = new float[] { 2.71f, 1.50f, 1.00f, 0.71f }, FinalDrive = 4.08f, ReverseRatio = 2.4f,
        Acc0100Lo = 7.5f, Acc0100Hi = 11.0f, TopLo = 215f, TopHi = 250f, BrakeLo = 38f, BrakeHi = 50f, SkidLo = 0.80f, SkidHi = 1.10f
    };

    static CarSpec Porsche992GT3() => new CarSpec
    {
        Name = "Porsche 911 GT3 (992)", Tagline = "1435 kg, 4.0 flat-six (375 kW, 9000 rpm), rear-engine RWD, 7-speed PDK, big wing", Style = BodyStyle.Sports, Layout = DriveLayout.RWD, Paint = new Color32(250, 200, 20, 255),
        Length = 4.573f, Width = 1.852f, Height = 1.279f, Wheelbase = 2.457f, TrackF = 1.63f, TrackR = 1.58f, WheelR = 0.340f, TyreWidthF = 0.255f, TyreWidthR = 0.315f, FrontOverhang = 0.93f, RearOverhang = 1.19f,
        Mass = 1435f, FrontWeight = 0.38f, CgHeight = 0.47f,
        RideFreqF = 2.05f, RideFreqR = 2.30f, ArbF = 42000f, ArbR = 30000f, DampBumpRatio = 0.35f, DampReboundRatio = 0.62f,
        MuScale = 1.28f, MuFrontScale = 1.07f, MuRearScale = 1.05f, YawInertiaScale = 1.25f, BrakeF = 2500f, BrakeR = 1400f, CdA = 0.70f, ClA = 1.0f, ClFront = 0.35f, MaxSteerDeg = 32f, LsdPreload = 160f,
        TqRpm = new float[] { 0, 1000, 2000, 3000, 4000, 5000, 6100, 7000, 8000, 8400, 9000, 9500 }, TqNm = new float[] { 100, 300, 360, 400, 430, 455, 470, 455, 430, 425, 400, 300 },
        Idle = 1000f, Redline = 9000f, EngineInertia = 0.20f, Efficiency = 0.94f, Cylinders = 6,
        Ratios = new float[] { 3.75f, 2.38f, 1.72f, 1.34f, 1.08f, 0.88f, 0.62f }, FinalDrive = 4.10f, ReverseRatio = 3.4f,
        Acc0100Lo = 2.8f, Acc0100Hi = 4.6f, TopLo = 290f, TopHi = 335f, BrakeLo = 24f, BrakeHi = 36f, SkidLo = 1.25f, SkidHi = 1.80f
    };
}
