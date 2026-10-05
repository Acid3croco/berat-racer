#include "BeratTraffic.h"

#include "BeratLaneGraph.h"
#include "BeratTimeOfDay.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"

namespace
{
	// Intelligent Driver Model (Treiber), in cm and s.
	constexpr float MaxAccel = 160.f;        // a
	constexpr float ComfortBrake = 250.f;    // b
	constexpr float MinGap = 250.f;          // s0
	constexpr float Headway = 1.4f;          // T
	constexpr float LookAhead = 15000.f;     // how far a car looks for leaders and stop lines

	float Idm(float V, float V0, float Gap, float DeltaV)
	{
		V0 = FMath::Max(V0, 1.f);
		const float SStar = MinGap + FMath::Max(0.f, V * Headway + V * DeltaV / (2.f * FMath::Sqrt(MaxAccel * ComfortBrake)));
		return MaxAccel * (1.f - FMath::Pow(V / V0, 4.f) - FMath::Square(SStar / FMath::Max(Gap, 1.f)));
	}
}

ABeratTraffic::ABeratTraffic()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.TickGroup = TG_PrePhysics;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void ABeratTraffic::BeginPlay()
{
	Super::BeginPlay();
	Cars.SetNum(MaxCars);
	for (int32 i = 0; i < Cars.Num(); ++i)
	{
		UStaticMeshComponent* B = NewObject<UStaticMeshComponent>(this);
		B->SetMobility(EComponentMobility::Movable);
		B->SetupAttachment(RootComponent);
		B->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
		B->SetCollisionObjectType(ECC_Vehicle);
		B->SetSimulatePhysics(false);
		B->SetVisibility(false);
		B->RegisterComponent();
		Cars[i].Body = B;
	}
}

void ABeratTraffic::Tick(float Dt)
{
	Super::Tick(Dt);
	if (!Graph || Graph->Lanes.Num() == 0 || Models.Num() == 0 || Dt <= 0.f)
	{
		return;
	}
	Dt = FMath::Min(Dt, 0.1f);
	Time += Dt;
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
	{
		FRotator R;
		PC->GetPlayerViewPoint(ViewPos, R);
		ViewDir = R.Vector();
	}

	OnLane.Reset();
	for (int32 i = 0; i < Cars.Num(); ++i)
	{
		if (Cars[i].bActive)
		{
			OnLane.FindOrAdd(Cars[i].Lane).Add(i);
		}
	}

	for (int32 i = 0; i < Cars.Num(); ++i)
	{
		FBeratTrafficCar& C = Cars[i];
		if (!C.bActive)
		{
			continue;
		}
		const FBeratLane& L = Graph->Lanes[C.Lane];
		// Allowed speed here: the graph's per-point speed (curvature and limit), times the driver's mood.
		FVector P, D;
		Graph->Sample(C.Lane, C.S, P, D);
		const int32 Pt = FMath::Clamp(Algo::LowerBound(L.Distance, C.S), 0, L.Speed.Num() - 1);
		float V0 = (L.Speed.IsValidIndex(Pt) ? L.Speed[Pt] * 100.f : L.LimitKmh / 0.036f) * C.Desired;
		// at night, a little slower
		V0 *= 1.f - 0.08f * ABeratTimeOfDay::GetNightFactor(this);

		float LeaderSpeed = 0.f;
		const float Gap = GapAhead(i, LeaderSpeed);
		float A = Idm(C.Speed, V0, Gap, C.Speed - LeaderSpeed);
		const float Stop = StopDistance(i);
		if (Stop < LookAhead)
		{
			A = FMath::Min(A, Idm(C.Speed, V0, Stop, C.Speed));
		}
		C.Speed = FMath::Max(0.f, C.Speed + FMath::Clamp(A, -900.f, MaxAccel) * Dt);
		C.S += C.Speed * Dt;
		if (C.Speed < 10.f && Stop < 600.f)
		{
			C.Stopped += Dt;
		}

		// Next lane(s).
		while (C.S > Graph->Lanes[C.Lane].Length())
		{
			const int32 Next = C.NextLane != INDEX_NONE ? C.NextLane : PickNext(C.Lane);
			if (Next == INDEX_NONE)
			{
				C.bActive = false;      // dead end or the package's border
				break;
			}
			C.S -= Graph->Lanes[C.Lane].Length();
			C.Lane = Next;
			C.NextLane = PickNext(Next);
			C.Stopped = 0.f;
		}
		if (C.bActive && FVector::DistSquared(Graph->Lanes[C.Lane].Points[0], ViewPos) > FMath::Square(DespawnAt))
		{
			C.bActive = false;
		}
		if (C.bActive)
		{
			Place(C);
		}
		else
		{
			C.Body->SetVisibility(false);
			C.Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		}
	}

	SpawnClock += Dt;
	if (SpawnClock > 0.25f)
	{
		SpawnClock = 0.f;
		SpawnSome();
	}
}

void ABeratTraffic::Place(FBeratTrafficCar& C)
{
	// Front and rear axle points on the lane: the body pitches with the road and turns smoothly through bends.
	const float Half = C.Length * 0.32f;
	FVector Pf, Pr, D;
	Graph->Sample(C.Lane, C.S + Half, Pf, D);
	Graph->Sample(C.Lane, FMath::Max(C.S - Half, 0.f), Pr, D);
	const FVector Dir = (Pf - Pr).GetSafeNormal();
	const FVector Mid = (Pf + Pr) * 0.5f;
	C.Body->SetWorldLocationAndRotation(Mid, Dir.Rotation(), false, nullptr, ETeleportType::TeleportPhysics);
}

float ABeratTraffic::GapAhead(int32 Car, float& OutLeaderSpeed) const
{
	// Nearest car ahead on this lane or the next lanes it will take, within LookAhead.
	const FBeratTrafficCar& C = Cars[Car];
	float Best = LookAhead, Offset = -C.S;
	int32 Lane = C.Lane, NextGuess = C.NextLane;
	for (int32 Hop = 0; Hop < 4 && Lane != INDEX_NONE && Offset < LookAhead; ++Hop)
	{
		if (const TArray<int32>* On = OnLane.Find(Lane))
		{
			for (int32 j : *On)
			{
				if (j == Car)
				{
					continue;
				}
				const float Ahead = Offset + Cars[j].S - Cars[j].Length * 0.5f - C.Length * 0.5f;
				if (Ahead > -C.Length * 0.25f && Ahead < Best)
				{
					Best = FMath::Max(Ahead, 1.f);
					OutLeaderSpeed = Cars[j].Speed;
				}
			}
		}
		Offset += Graph->Lanes[Lane].Length();
		Lane = Hop == 0 ? NextGuess : PickNext(Lane);
	}
	// The player's car counts as a leader when it sits in front on the lane.
	if (APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
	{
		FVector P, D;
		Graph->Sample(C.Lane, C.S, P, D);
		const FVector To = Player->GetActorLocation() - P;
		const float Along = FVector::DotProduct(To, D);
		const float Across = (To - D * Along).Size2D();
		if (Along > 0.f && Along < Best && Across < 250.f)
		{
			Best = FMath::Max(Along - C.Length, 1.f);
			OutLeaderSpeed = FMath::Max(0.f, FVector::DotProduct(Player->GetVelocity(), D));
		}
	}
	return Best;
}

bool ABeratTraffic::SignalGreen(const FBeratLane& Connector) const
{
	// 60 s cycle per junction: two phases split by the connector's heading, 3 s all-red between.
	const int32 J = Connector.Junction;
	const double Phase = FMath::Fmod(Time + (J * 7919 % 60), 60.0);
	FVector D = Connector.Points.Num() > 1 ? (Connector.Points[1] - Connector.Points[0]) : FVector::ForwardVector;
	const bool bNorthSouth = FMath::Abs(D.Y) > FMath::Abs(D.X);
	return bNorthSouth ? (Phase < 27.0) : (Phase >= 30.0 && Phase < 57.0);
}

float ABeratTraffic::StopDistance(int32 Car) const
{
	// Distance to the end of the current lane when the next connector may not be entered yet.
	const FBeratTrafficCar& C = Cars[Car];
	const FBeratLane& L = Graph->Lanes[C.Lane];
	const float ToEnd = L.Length() - C.S - C.Length * 0.5f;
	if (ToEnd > LookAhead || C.NextLane == INDEX_NONE)
	{
		return LookAhead;
	}
	const FBeratLane& N = Graph->Lanes[C.NextLane];
	if (N.Kind != EBeratLaneKind::Connector)
	{
		return LookAhead;
	}
	switch (N.Control)
	{
	case EBeratControl::Signals:
		return SignalGreen(N) ? LookAhead : FMath::Max(ToEnd, 1.f);
	case EBeratControl::Stop:
		if (C.Stopped < 1.5f)
		{
			return FMath::Max(ToEnd, 1.f);
		}
		break;
	default:
		break;
	}
	// Give way: wait while a connector this one yields to is occupied (or a car is about to enter it).
	for (int32 Y : N.Yields)
	{
		if (const TArray<int32>* On = OnLane.Find(Y))
		{
			if (On->Num() > 0)
			{
				return FMath::Max(ToEnd, 1.f);
			}
		}
	}
	return LookAhead;
}

int32 ABeratTraffic::PickNext(int32 Lane) const
{
	const TArray<int32>& Next = Graph->Lanes[Lane].Next;
	if (Next.Num() == 0)
	{
		return INDEX_NONE;
	}
	// Prefer going on along important roads; skip U-turns and lane changes most of the time.
	float Total = 0.f;
	TArray<float, TInlineAllocator<8>> W;
	for (int32 N : Next)
	{
		const FBeratLane& L = Graph->Lanes[N];
		float Weight = 1.f / FMath::Max<float>(L.Importance, 1.f);
		if (L.Kind == EBeratLaneKind::UTurn)
		{
			Weight *= 0.02f;
		}
		else if (L.Kind == EBeratLaneKind::Change)
		{
			Weight *= 0.1f;
		}
		else if (FMath::Abs(L.Turn) < 30.f)
		{
			Weight *= 2.f;
		}
		if (L.bDirt)
		{
			Weight *= 0.05f;
		}
		W.Add(Weight);
		Total += Weight;
	}
	float R = FMath::FRand() * Total;
	for (int32 k = 0; k < Next.Num(); ++k)
	{
		R -= W[k];
		if (R <= 0.f)
		{
			return Next[k];
		}
	}
	return Next.Last();
}

void ABeratTraffic::SpawnSome()
{
	int32 Budget = 6;
	for (int32 i = 0; i < Cars.Num() && Budget > 0; ++i)
	{
		if (!Cars[i].bActive)
		{
			TrySpawn(i);
			--Budget;
		}
	}
}

bool ABeratTraffic::TrySpawn(int32 Car)
{
	// A random lane in a random cell of the ring around the viewer, weighted by the road's density.
	const float R = FMath::FRandRange(SpawnMin, SpawnMax);
	const float Ang = FMath::FRandRange(0.f, 2.f * PI);
	const FVector At = ViewPos + FVector(FMath::Cos(Ang), FMath::Sin(Ang), 0.f) * R;
	const FBeratLaneCell* Cell = Graph->Cells.Find(UBeratLaneGraph::CellOf(At));
	if (!Cell || Cell->Lanes.Num() == 0)
	{
		return false;
	}
	const int32 L = Cell->Lanes[FMath::RandRange(0, Cell->Lanes.Num() - 1)];
	const FBeratLane& Lane = Graph->Lanes[L];
	if (Lane.Kind != EBeratLaneKind::Lane || Lane.Length() < 2000.f)
	{
		return false;
	}
	const int32 Imp = FMath::Clamp<int32>(Lane.Importance, 1, DensityPerKm.Num()) - 1;
	if (FMath::FRand() * 6.f > DensityPerKm[Imp])
	{
		return false;
	}
	FBeratTrafficCar& C = Cars[Car];
	C.Lane = L;
	C.S = FMath::FRandRange(0.f, Lane.Length());
	FVector P, D;
	Graph->Sample(L, C.S, P, D);
	// Out of sight: behind the viewer, or far.
	const FVector To = P - ViewPos;
	if (FVector::DotProduct(To.GetSafeNormal(), ViewDir) > 0.5f && To.Size() < 60000.f)
	{
		return false;
	}
	// Not on top of another car.
	if (const TArray<int32>* On = OnLane.Find(L))
	{
		for (int32 j : *On)
		{
			if (FMath::Abs(Cars[j].S - C.S) < 2500.f)
			{
				return false;
			}
		}
	}
	C.Model = FMath::RandRange(0, Models.Num() - 1);
	C.Body->SetStaticMesh(Models[C.Model]);
	C.Length = FMath::Max(C.Body->Bounds.BoxExtent.X * 2.f, 380.f);
	if (const UStaticMesh* M = Models[C.Model])
	{
		C.Length = M->GetBounds().BoxExtent.X * 2.f;
	}
	C.Desired = FMath::FRandRange(0.85f, 1.08f);
	C.Speed = (Lane.Speed.Num() ? Lane.Speed[0] * 100.f : 1300.f) * C.Desired * 0.8f;
	C.NextLane = PickNext(L);
	C.Stopped = 0.f;
	C.bActive = true;
	C.Body->SetVisibility(true);
	C.Body->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
	Place(C);
	OnLane.FindOrAdd(L).Add(Car);
	return true;
}
