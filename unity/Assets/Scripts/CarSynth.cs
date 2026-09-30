using System;

/// <summary>
/// Procedural car audio, no assets: engine (4-cylinder-ish pulse train + harmonics through a load-dependent low-pass),
/// wind (noise rising with speed^2), road rumble, tyre squeal (band-passed noise driven by slip).
/// Fill() runs on the audio thread; the public fields are written from the main thread.
/// </summary>
public class CarSynth
{
    public float Rpm = 900f, Load, SpeedMs, Slip, Rough = 1f, Master = 0.15f;
    public float WindGain = 1f;                          // wind is what the driver hears; heard from outside the car it is much quieter
    public bool Muted; public float PulsesPerRev = 2f, Brightness = 1f;
    public float LastPeak; public int Buffers; public bool NaNSeen;

    float rpmS = 900f, loadS, speedS, slipS, masterS;
    double phase, lfo;
    float engLp1, engLp2, windLp1, windLp2, rumbleLp, sqA, sqB, sqC;
    uint rng = 22222;

    float Noise() { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return (rng & 0xFFFFFF) / 8388608f - 1f; }

    static float Coef(float cutoff, float sr) => 1f - (float)Math.Exp(-2.0 * Math.PI * cutoff / sr);

    public void Fill(float[] data, int channels, int sampleRate)
    {
        float sr = sampleRate; int frames = data.Length / channels; float peak = 0f;
        float k = 1f - (float)Math.Exp(-1.0 / (0.02 * sr));                       // 20 ms parameter smoothing
        for (int n = 0; n < frames; n++)
        {
            rpmS += (Rpm - rpmS) * k; loadS += (Load - loadS) * k; speedS += (SpeedMs - speedS) * k; slipS += (Slip - slipS) * k;
            masterS += ((Muted ? 0f : Master) - masterS) * k;

            // ---- engine: 4-stroke 4-cyl fires twice per revolution
            float f0 = rpmS / 60f * PulsesPerRev;
            phase += f0 / sr; if (phase >= 1.0) phase -= 1.0;
            float p = (float)phase, tp = p * 6.2831853f;
            float saw = 2f * p - 1f;
            float pulse = (float)Math.Sin(tp) + 0.55f * (float)Math.Sin(2 * tp + 0.6f) + 0.35f * (float)Math.Sin(3 * tp + 1.3f)
                        + (0.15f + 0.5f * loadS) * (float)Math.Sin(4 * tp) + (0.10f + 0.4f * loadS) * (float)Math.Sin(6 * tp + 0.4f) + (0.05f + 0.3f * loadS) * (float)Math.Sin(8 * tp);
            float raw = 0.5f * saw + 0.55f * pulse + 0.06f * Noise() * (0.3f + loadS);
            float cutoff = (500f + rpmS * 0.55f + 2600f * loadS) * Brightness;
            float c = Coef(cutoff, sr);
            engLp1 += (raw - engLp1) * c; engLp2 += (engLp1 - engLp2) * c;
            float engine = engLp2 * (0.32f + 0.5f * loadS) * (0.8f + 0.2f * Math.Min(1f, rpmS / 6000f));

            // ---- wind
            float sp = Math.Min(speedS / 70f, 1.6f);
            float wn = Noise();
            float wc = Coef(250f + speedS * 38f, sr);
            windLp1 += (wn - windLp1) * wc; windLp2 += (windLp1 - windLp2) * wc;
            float wind = windLp2 * sp * sp * 1.5f * WindGain;

            // ---- road rumble (rougher on dirt / grass)
            rumbleLp += (Noise() - rumbleLp) * Coef(140f, sr);
            float rumble = rumbleLp * Math.Min(1f, speedS / 40f) * 0.55f * Rough;

            // ---- tyre squeal: band-limited noise with slow wobble
            lfo += 7.0 / sr; if (lfo > 1.0) lfo -= 1.0;
            float wob = 0.75f + 0.25f * (float)Math.Sin(lfo * 6.2831853);
            float sn = Noise();
            sqA += (sn - sqA) * Coef(2600f, sr); sqB += (sn - sqB) * Coef(900f, sr);
            float squeal = (sqA - sqB) * 3.0f * wob * Math.Max(0f, slipS - 0.35f) * Math.Min(1f, speedS / 8f);

            float s = (engine + wind + rumble + squeal) * masterS;
            s = (float)Math.Tanh(s * 1.2f);
            if (float.IsNaN(s) || float.IsInfinity(s)) { s = 0f; NaNSeen = true; }
            float a = Math.Abs(s); if (a > peak) peak = a;
            for (int ch = 0; ch < channels; ch++) data[n * channels + ch] = s;
        }
        LastPeak = peak; Buffers++;
    }
}
