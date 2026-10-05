#include "BeratEngineSound.h"

UBeratEngineSound::UBeratEngineSound(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	NumChannels = 1;
	bAllowSpatialization = false;      // the player's own car: heard in the cabin / behind it, not placed in the world
}

bool UBeratEngineSound::Init(int32& SampleRate)
{
	Rate = float(SampleRate);
	NumChannels = 1;
	return true;
}

int32 UBeratEngineSound::OnGenerateAudio(float* Out, int32 NumSamples)
{
	const float TwoPi = 6.2831853f;
	const float RpmT = FMath::Clamp(Rpm.load(), 500.f, 9000.f), LoadT = FMath::Clamp(Load.load(), 0.f, 1.f);
	const float SlipT = FMath::Clamp(Slip.load(), 0.f, 1.f), SpeedT = FMath::Clamp(Speed.load(), 0.f, 300.f);
	const float K = 1.f / Rate;
	for (int32 i = 0; i < NumSamples; ++i)
	{
		// parameters glide (no zipper noise)
		RpmS += (RpmT - RpmS) * 0.0008f;
		LoadS += (LoadT - LoadS) * 0.0006f;
		SlipS += (SlipT - SlipS) * 0.0005f;
		SpeedS += (SpeedT - SpeedS) * 0.0002f;
		// firing frequency: a four-stroke fires every cylinder once in two turns
		const float F = RpmS / 60.f * Cylinders * 0.5f;
		Phase += F * K;
		if (Phase > 1024.0) { Phase -= 1024.0; }
		const float P = float(FMath::Fmod(Phase, 1.0)) * TwoPi;
		const float Crank = float(FMath::Fmod(Phase * 0.5, 1.0)) * TwoPi;
		float E = FMath::Sin(P) + 0.55f * FMath::Sin(2.f * P + 0.4f) + 0.38f * FMath::Sin(3.f * P + 1.1f)
			+ 0.22f * FMath::Sin(4.f * P + 0.2f) + 0.12f * FMath::Sin(6.f * P + 2.f) + 0.45f * FMath::Sin(Crank);
		// combustion rasp: noise gated by the firing pulse, more under load
		const float Pulse = FMath::Pow(0.5f + 0.5f * FMath::Cos(P), 6.f);
		NoiseLp += (Noise() - NoiseLp) * 0.35f;
		E += NoiseLp * Pulse * (0.4f + 1.4f * LoadS);
		// brightness: two one-pole low-passes, cutoff rising with load and rpm
		const float Cut = 280.f + 900.f * LoadS + RpmS * 0.35f;
		const float A = 1.f - FMath::Exp(-TwoPi * Cut * K);
		Lp1 += (E - Lp1) * A;
		Lp2 += (Lp1 - Lp2) * A;
		float S = Lp2 * (0.32f + 0.5f * LoadS) * (0.6f + RpmS / 9000.f);
		// tyre squeal: a wavering ~1 kHz tone
		if (SlipS > 0.01f)
		{
			SquealMod += 4.3f * K;
			SquealPhase += (950.f + 120.f * FMath::Sin(TwoPi * float(SquealMod))) * K;
			if (SquealPhase > 1024.0) { SquealPhase -= 1024.0; }
			S += 0.18f * SlipS * FMath::Sin(TwoPi * float(FMath::Fmod(SquealPhase, 1.0)));
		}
		// wind: low noise with speed squared
		WindLp += (Noise() - WindLp) * 0.06f;
		WindLp2 += (WindLp - WindLp2) * 0.06f;
		S += WindLp2 * 2.5f * FMath::Square(SpeedS / 160.f);
		Out[i] = FMath::Clamp(S * Volume, -1.f, 1.f);
	}
	return NumSamples;
}
