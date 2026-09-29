using UnityEngine;

/// <summary>Drives CarSynth from the car: speed -> simulated gearbox -> RPM, throttle -> load, tyre slip -> squeal.</summary>
[RequireComponent(typeof(AudioSource))]
public class CarAudio : MonoBehaviour
{
    public CarController Car;
    public readonly CarSynth Synth = new CarSynth();
    public float Volume = 0.15f;                  // master volume (15% by default; [ and ] change it)
    public int Gear { get; private set; } = 1;
    public float Rpm => Synth.Rpm;
    int sampleRate = 48000;
    const float Idle = 900f, Redline = 7000f;
    static readonly float[] GearTop = { 55f, 95f, 135f, 175f, 215f, 260f };      // km/h at redline in each gear
    float shiftTimer, prevThrottle;

    void Start()
    {
        sampleRate = AudioSettings.outputSampleRate;
        var src = GetComponent<AudioSource>();
        src.clip = AudioClip.Create("carsynth-carrier", sampleRate, 1, sampleRate, false);
        src.clip.SetData(new float[sampleRate], 0);
        src.loop = true; src.spatialBlend = 0f; src.playOnAwake = false; src.Play();
        Log.I("audio", $"synth ready: output {sampleRate} Hz, speaker mode {AudioSettings.speakerMode}");
    }

    void OnAudioFilterRead(float[] data, int channels) => Synth.Fill(data, channels, sampleRate);

    void Update()
    {
        if (Car == null) return;
        bool paused = Time.timeScale == 0f;
        float kmh = Car.SpeedKmh, thr = Car.Throttle;
        // gearbox with hysteresis
        float gtop = GearTop[Gear - 1];
        if (kmh > gtop * 0.94f && Gear < GearTop.Length) { Gear++; shiftTimer = 0.25f; }
        else if (Gear > 1 && kmh < GearTop[Gear - 2] * 0.60f) { Gear--; shiftTimer = 0.15f; }
        gtop = GearTop[Gear - 1];
        float frac = Mathf.Clamp01(kmh / gtop);
        float target = Mathf.Lerp(Idle + 900f, Redline, Mathf.Pow(frac, 0.9f));
        if (kmh < 12f) target = Mathf.Max(target, Idle + thr * 5200f);                // launch: revs follow the throttle
        shiftTimer -= Time.unscaledDeltaTime;
        if (shiftTimer > 0f) target *= 0.72f;                                         // drop between gears
        if (thr < 0.05f && kmh < 4f) target = Idle;
        Synth.Rpm = Mathf.Lerp(Synth.Rpm, target, 1f - Mathf.Exp(-9f * Time.unscaledDeltaTime));
        Synth.Load = paused ? 0f : Mathf.Clamp01(Mathf.Lerp(0.15f, 1f, thr));
        Synth.SpeedMs = paused ? 0f : Car.Body.linearVelocity.magnitude;
        Synth.Slip = paused ? 0f : Car.SlipAmount;
        Synth.Rough = Car.CurrentSurface == Surface.Asphalt ? 1f : Car.CurrentSurface == Surface.Dirt ? 2.2f : 1.8f;
        Synth.Master = paused ? Volume * 0.2f : Volume;
        prevThrottle = thr;
    }
}
