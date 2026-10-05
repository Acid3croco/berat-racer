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
	// NOAA's low-precision solar position (good to a fraction of a degree).
	const double Gamma = 2.0 * PI / 365.0 * (DayOfYear - 1 + (Hours - UtcOffsetHours - 12.0) / 24.0);
	const double EqTime = 229.18 * (0.000075 + 0.001868 * FMath::Cos(Gamma) - 0.032077 * FMath::Sin(Gamma)
		- 0.014615 * FMath::Cos(2 * Gamma) - 0.040849 * FMath::Sin(2 * Gamma));
	const double Decl = 0.006918 - 0.399912 * FMath::Cos(Gamma) + 0.070257 * FMath::Sin(Gamma) - 0.006758 * FMath::Cos(2 * Gamma)
		+ 0.000907 * FMath::Sin(2 * Gamma) - 0.002697 * FMath::Cos(3 * Gamma) + 0.00148 * FMath::Sin(3 * Gamma);
	const double TrueSolarMin = (Hours - UtcOffsetHours) * 60.0 + EqTime + 4.0 * LongitudeDeg;
	const double HourAngle = FMath::DegreesToRadians(TrueSolarMin / 4.0 - 180.0);
	const double Lat = FMath::DegreesToRadians(LatitudeDeg);
	// East-north-up vector of the sun.
	const double CosD = FMath::Cos(Decl);
	const double E = -CosD * FMath::Sin(HourAngle);
	const double N = FMath::Cos(Lat) * FMath::Sin(Decl) - FMath::Sin(Lat) * CosD * FMath::Cos(HourAngle);
	const double U = FMath::Sin(Lat) * FMath::Sin(Decl) + FMath::Cos(Lat) * CosD * FMath::Cos(HourAngle);
	return FVector(E, N, U).GetSafeNormal();
}

void ABeratTimeOfDay::Apply(bool bForceCapture)
{
	const FVector S = SunDirection();                       // package axes
	const FVector SunUE(S.X, -S.Y, S.Z);                    // Unreal axes, towards the sun
	const float Elev = FMath::RadiansToDegrees(FMath::Asin(S.Z));
	const float Night = 1.f - FMath::SmoothStep(-6.f, 4.f, Elev);

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
