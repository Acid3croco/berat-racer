#include "BeratTimeOfDay.h"

#include "Components/DirectionalLightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/SkyLightComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/SkyLight.h"
#include "Engine/World.h"
#include "Kismet/KismetMaterialLibrary.h"
#include "Materials/MaterialParameterCollection.h"
#include "SunPosition.h"

ABeratTimeOfDay::ABeratTimeOfDay()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.TickGroup = TG_PrePhysics;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

float ABeratTimeOfDay::GetNightFactor(const UObject* WorldContext)
{
	const UWorld* W = WorldContext ? WorldContext->GetWorld() : nullptr;
	const UBeratLightingSubsystem* S = W ? W->GetSubsystem<UBeratLightingSubsystem>() : nullptr;
	return S ? S->Night : 0.f;
}

void ABeratTimeOfDay::SkipHours(float Delta)
{
	Hours = FMath::Fmod(Hours + Delta + 24.f, 24.f);
	Apply(true);
}

void ABeratTimeOfDay::BeginPlay()
{
	Super::BeginPlay();
	Apply(true);
}

void ABeratTimeOfDay::OnConstruction(const FTransform& Transform)
{
	Super::OnConstruction(Transform);
	Apply(true);
}

void ABeratTimeOfDay::Tick(float Dt)
{
	Super::Tick(Dt);
	if (GetWorld()->IsGameWorld() && Rate > 0.f)
	{
		Hours += Dt * Rate / 60.f;
		if (Hours >= 24.f)
		{
			Hours -= 24.f;
			DayOfYear = DayOfYear % 365 + 1;
		}
	}
	Apply(false);
}

FVector ABeratTimeOfDay::SunDirection() const
{
	// Epic's Sun Position plugin: elevation and azimuth (degrees clockwise from north) for the place, date and clock.
	const FDateTime Date = FDateTime(2026, 1, 1) + FTimespan::FromDays(DayOfYear - 1);
	const int32 H = FMath::FloorToInt(Hours);
	const int32 Min = FMath::FloorToInt((Hours - H) * 60.f);
	const int32 Sec = FMath::FloorToInt(((Hours - H) * 60.f - Min) * 60.f);
	FSunPositionData Data;
	USunPositionFunctionLibrary::GetSunPosition(LatitudeDeg, LongitudeDeg, UtcOffsetHours, false, Date.GetYear(), Date.GetMonth(),
		Date.GetDay(), H, Min, Sec, Data);
	// the plugin returns the elevation plus 180 degrees (its own convention for aiming a directional light)
	const double El = FMath::DegreesToRadians(Data.CorrectedElevation - 180.0), Az = FMath::DegreesToRadians(Data.Azimuth);
	// East-north-up.
	return FVector(FMath::Sin(Az) * FMath::Cos(El), FMath::Cos(Az) * FMath::Cos(El), FMath::Sin(El));
}

void ABeratTimeOfDay::Apply(bool bForceCapture)
{
	const FVector S = SunDirection();                       // package axes
	const FVector SunUE(S.X, -S.Y, S.Z);                    // Unreal axes, towards the sun
	const float Elev = FMath::RadiansToDegrees(FMath::Asin(S.Z));
	const float Night = 1.f - FMath::SmoothStep(-6.f, 4.f, Elev);
	if (bForceCapture)
	{
		UE_LOG(LogTemp, Display, TEXT("[berat] time %.2f h, day %d: sun elevation %.1f deg, azimuth dir (%.2f, %.2f), night %.2f"),
			Hours, DayOfYear, Elev, S.X, S.Y, Night);
	}

	if (UWorld* W = GetWorld())
	{
		if (UBeratLightingSubsystem* Sub = W->GetSubsystem<UBeratLightingSubsystem>())
		{
			Sub->Night = Night;
			Sub->SunElevationDeg = Elev;
		}
		if (Parameters)
		{
			UKismetMaterialLibrary::SetScalarParameterValue(this, Parameters, TEXT("Night"), Night);
		}
	}
	if (Sun)
	{
		// A directional light shines along its forward vector: away from the sun.
		Sun->SetActorRotation((-SunUE).Rotation());
		UDirectionalLightComponent* L = Cast<UDirectionalLightComponent>(Sun->GetLightComponent());
		L->SetIntensity(SunLux * FMath::SmoothStep(-4.f, 2.f, Elev));
		L->SetVisibility(Elev > -5.f);
	}
	if (Moon)
	{
		// The moon roughly opposite the sun, lifted so it stands in the night sky.
		const FVector MoonUE = FVector(-SunUE.X, -SunUE.Y, FMath::Abs(SunUE.Z) * 0.6f + 0.35f).GetSafeNormal();
		Moon->SetActorRotation((-MoonUE).Rotation());
		UDirectionalLightComponent* L = Cast<UDirectionalLightComponent>(Moon->GetLightComponent());
		L->SetIntensity(MoonLux * Night);
		L->SetVisibility(Night > 0.02f);
	}
	if (Fog)
	{
		// Thinner, cooler fog at night so headlights and lamps carry.
		UExponentialHeightFogComponent* F = Fog->GetComponent();
		F->SetFogInscatteringColor(FMath::Lerp(FLinearColor(0.45f, 0.55f, 0.7f), FLinearColor(0.02f, 0.03f, 0.05f), Night));
	}
	if (Sky)
	{
		// Real-time capture when the light has changed enough (dusk changes fast) or every 2 s.
		CaptureClock += GetWorld() ? GetWorld()->GetDeltaSeconds() : 0.f;
		if (bForceCapture || FMath::Abs(Night - LastCaptureNight) > 0.02f || CaptureClock > 2.f)
		{
			Sky->GetLightComponent()->RecaptureSky();
			LastCaptureNight = Night;
			CaptureClock = 0.f;
		}
	}
}
