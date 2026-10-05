#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "Subsystems/WorldSubsystem.h"
#include "BeratTimeOfDay.generated.h"

class ADirectionalLight;
class ASkyLight;
class AExponentialHeightFog;
class UMaterialParameterCollection;

// Shared lighting state of a world: what the lamps, windows, car lights and traffic read.
UCLASS()
class BERATRACER_API UBeratLightingSubsystem : public UWorldSubsystem
{
	GENERATED_BODY()

public:
	// 0 in daylight, 1 at night (sun 6 degrees under the horizon), smooth across dusk and dawn.
	float Night = 0.f;
	float SunElevationDeg = 45.f;
};

// The sun and the moon over Bérat, from the date and the clock. Drives the two directional lights (the sky atmosphere uses
// them), the sky light's recapture, the fog and the "Night" parameter of the material parameter collection.
UCLASS()
class BERATRACER_API ABeratTimeOfDay : public AActor
{
	GENERATED_BODY()

public:
	ABeratTimeOfDay();

	// Local solar-ish clock: hours 0..24 (CEST, UTC+2 in summer).
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Time", meta = (ClampMin = 0, ClampMax = 24)) float Hours = 17.5f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Time", meta = (ClampMin = 1, ClampMax = 365)) int32 DayOfYear = 172;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Time") float UtcOffsetHours = 2.f;
	// Game minutes per real second (0 stops the clock). 1440 / Rate = real minutes per day: 24 -> one hour a day.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Time") float Rate = 24.f;
	UPROPERTY(EditAnywhere, Category = "Time") double LatitudeDeg = 43.38;
	UPROPERTY(EditAnywhere, Category = "Time") double LongitudeDeg = 1.17;

	UPROPERTY(EditAnywhere, Category = "Lights") TObjectPtr<ADirectionalLight> Sun;
	UPROPERTY(EditAnywhere, Category = "Lights") TObjectPtr<ADirectionalLight> Moon;
	UPROPERTY(EditAnywhere, Category = "Lights") TObjectPtr<ASkyLight> Sky;
	UPROPERTY(EditAnywhere, Category = "Lights") TObjectPtr<AExponentialHeightFog> Fog;
	UPROPERTY(EditAnywhere, Category = "Lights") TObjectPtr<UMaterialParameterCollection> Parameters;
	UPROPERTY(EditAnywhere, Category = "Lights") float SunLux = 100000.f;
	UPROPERTY(EditAnywhere, Category = "Lights") float MoonLux = 0.6f;

	UFUNCTION(BlueprintCallable, Category = "Time") void SkipHours(float Delta);

	static float GetNightFactor(const UObject* WorldContext);

	virtual void Tick(float DeltaSeconds) override;
	virtual bool ShouldTickIfViewportsOnly() const override { return true; }

protected:
	virtual void BeginPlay() override;
	virtual void OnConstruction(const FTransform& Transform) override;

private:
	void Apply(bool bForceCapture);
	// Direction towards the sun in package axes (x east, y north, z up), from the date, the clock and the place.
	FVector SunDirection() const;
	float LastCaptureNight = -1.f;
	float CaptureClock = 0.f;
};
