using UnityEngine;

/// <summary>Drives CarSynth from the car: speed -> simulated gearbox -> RPM, throttle -> load, tyre slip -> squeal.</summary>
[RequireComponent(typeof(AudioSource))]
public class CarAudio : MonoBehaviour
{
    public CarController Car;
    public readonly CarSynth Synth = new CarSynth();
    public float Volume = 0f;                     // master volume: 0% by default (the user's request); ] raises it in 5% steps, [ lowers it
    public int Gear { get; private set; } = 1;
    public float Rpm => Synth.Rpm;
    int sampleRate = 48000;

    void Start()
    {
        sampleRate = AudioSettings.outputSampleRate;
        if (Application.isBatchMode) { Volume = 0f; Log.I("audio", "batch / headless run: silent"); }      // automated tests stay nearly silent
        if (Car != null && Car.Spec != null) { Synth.PulsesPerRev = Car.Spec.Cylinders / 2f; Synth.Brightness = Car.Spec.Style == BodyStyle.Sports ? 1.35f : Car.Spec.Style == BodyStyle.Sedan ? 0.85f : 1f; }
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
        float thr = Car.Throttle;
        Gear = Car.Gear;
        Synth.Rpm = Mathf.Lerp(Synth.Rpm, Car.Rpm, 1f - Mathf.Exp(-25f * Time.unscaledDeltaTime));
        Synth.Load = paused ? 0f : Mathf.Clamp01(Mathf.Lerp(0.15f, 1f, Car.EngineLoad));
        Synth.SpeedMs = paused ? 0f : Car.Body.linearVelocity.magnitude;
        Synth.Slip = paused ? 0f : Car.SlipAmount;
        Synth.Rough = Car.CurrentSurface == Surface.Asphalt ? 1f : Car.CurrentSurface == Surface.Dirt ? 2.2f : 1.8f;
        Synth.Master = paused ? Volume * 0.2f : Volume;
    }
}
