#pragma once

#include "CoreMinimal.h"
#include "Components/SynthComponent.h"
#include <atomic>
#include "BeratEngineSound.generated.h"

// The car's sound, synthesized on the audio thread (the engine's synth component; no samples): a four-stroke engine's
// firing tone with harmonics and a crank sub-harmonic, brighter and raspier under load; tyre squeal from slip; wind from
// speed. The car sets the parameters each frame.
UCLASS(ClassGroup = Berat, meta = (BlueprintSpawnableComponent))
class BERATRACER_API UBeratEngineSound : public USynthComponent
{
	GENERATED_BODY()

public:
	UBeratEngineSound(const FObjectInitializer& ObjectInitializer);

	UPROPERTY(EditAnywhere, Category = "Berat") float Cylinders = 4.f;
	UPROPERTY(EditAnywhere, Category = "Berat") float Volume = 0.5f;

	void SetState(float InRpm, float InLoad, float InSlip, float InSpeedKmh)
	{
		Rpm.store(InRpm); Load.store(InLoad); Slip.store(InSlip); Speed.store(InSpeedKmh);
	}

protected:
	virtual bool Init(int32& SampleRate) override;
	virtual int32 OnGenerateAudio(float* OutAudio, int32 NumSamples) override;

private:
	std::atomic<float> Rpm{900.f}, Load{0.f}, Slip{0.f}, Speed{0.f};
	// audio-thread state
	float Rate = 48000.f;
	double Phase = 0.0, SquealPhase = 0.0, SquealMod = 0.0;
	float RpmS = 900.f, LoadS = 0.f, SlipS = 0.f, SpeedS = 0.f;
	float Lp1 = 0.f, Lp2 = 0.f, NoiseLp = 0.f, WindLp = 0.f, WindLp2 = 0.f;
	uint32 Seed = 22222;
	float Noise() { Seed = Seed * 1664525u + 1013904223u; return float(Seed >> 8) / float(1 << 24) * 2.f - 1.f; }
};
