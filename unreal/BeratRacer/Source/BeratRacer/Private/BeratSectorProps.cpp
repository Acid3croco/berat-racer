#include "BeratSectorProps.h"

#include "BeratTimeOfDay.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"

ABeratSectorProps::ABeratSectorProps()
{
	PrimaryActorTick.bCanEverTick = false;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	RootComponent->SetMobility(EComponentMobility::Static);
}

UHierarchicalInstancedStaticMeshComponent* ABeratSectorProps::AddInstances(UStaticMesh* Mesh, FName Name, float CullDistance, bool bCollision)
{
	UHierarchicalInstancedStaticMeshComponent* C = NewObject<UHierarchicalInstancedStaticMeshComponent>(this, Name);
	C->SetStaticMesh(Mesh);
	C->SetMobility(EComponentMobility::Static);
	C->SetupAttachment(RootComponent);
	C->SetCullDistances(0, CullDistance);
	C->SetCollisionEnabled(bCollision ? ECollisionEnabled::QueryAndPhysics : ECollisionEnabled::NoCollision);
	C->SetCastShadow(true);
	C->bAffectDistanceFieldLighting = true;
	AddInstanceComponent(C);
	C->RegisterComponent();
	return C;
}

void ABeratSectorProps::BeginPlay()
{
	Super::BeginPlay();
	if (UBeratLampSubsystem* S = GetWorld()->GetSubsystem<UBeratLampSubsystem>())
	{
		S->Sectors.Add(this);
	}
}

void ABeratSectorProps::EndPlay(const EEndPlayReason::Type Reason)
{
	if (UBeratLampSubsystem* S = GetWorld()->GetSubsystem<UBeratLampSubsystem>())
	{
		S->Sectors.Remove(this);
	}
	Super::EndPlay(Reason);
}

ABeratLampLights::ABeratLampLights()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.TickGroup = TG_PostUpdateWork;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void ABeratLampLights::BeginPlay()
{
	Super::BeginPlay();
	for (int32 i = 0; i < PoolSize; ++i)
	{
		UPointLightComponent* L = NewObject<UPointLightComponent>(this);
		L->SetMobility(EComponentMobility::Movable);
		L->SetupAttachment(RootComponent);
		L->SetIntensityUnits(ELightUnits::Candelas);
		L->SetIntensity(Candela);
		L->SetAttenuationRadius(Radius);
		L->SetLightColor(Colour);
		L->SetSourceRadius(15.f);
		L->SetCastShadows(i < ShadowedNearest);
		L->SetVisibility(false);
		L->RegisterComponent();
		Pool.Add(L);
	}
}

void ABeratLampLights::Tick(float Dt)
{
	Super::Tick(Dt);
	const float Night = ABeratTimeOfDay::GetNightFactor(this);
	Clock += Dt;
	if (Night < 0.05f)
	{
		for (UPointLightComponent* L : Pool)
		{
			L->SetVisibility(false);
		}
		return;
	}
	if (Clock < 0.2f)
	{
		return;
	}
	Clock = 0.f;

	FVector View = FVector::ZeroVector;
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
	{
		FRotator R;
		PC->GetPlayerViewPoint(View, R);
	}
	// The nearest lamp heads within reach, from the loaded sectors.
	const double Reach = 1200.0 * 100.0;
	TArray<TPair<double, FVector>> Near;
	Near.Reserve(1024);
	for (const TWeakObjectPtr<ABeratSectorProps>& W : GetWorld()->GetSubsystem<UBeratLampSubsystem>()->Sectors)
	{
		if (const ABeratSectorProps* S = W.Get())
		{
			for (const FVector& H : S->LampHeads)
			{
				const double D2 = FVector::DistSquared(H, View);
				if (D2 < Reach * Reach)
				{
					Near.Emplace(D2, H);
				}
			}
		}
	}
	const int32 N = FMath::Min(Near.Num(), Pool.Num());
	if (Near.Num() > N)
	{
		Algo::NthElement(Near, N, [](const auto& A, const auto& B) { return A.Key < B.Key; });
	}
	Near.SetNum(N);
	Near.Sort([](const auto& A, const auto& B) { return A.Key < B.Key; });
	for (int32 i = 0; i < Pool.Num(); ++i)
	{
		UPointLightComponent* L = Pool[i];
		if (i < N)
		{
			L->SetWorldLocation(Near[i].Value);
			// the far end of the pool fades so lights do not pop as they are reassigned
			const float Fade = 1.f - FMath::SmoothStep(0.7f, 1.f, float(i) / Pool.Num());
			L->SetIntensity(Candela * Night * Fade);
			L->SetVisibility(true);
		}
		else
		{
			L->SetVisibility(false);
		}
	}
}
