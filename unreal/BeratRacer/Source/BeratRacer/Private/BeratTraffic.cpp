#include "BeratTraffic.h"

#include "BeratLaneGraph.h"
#include "BeratTimeOfDay.h"
#include "Components/StaticMeshComponent.h"
#include "Components/SpotLightComponent.h"
#include "EngineUtils.h"
#include "Materials/MaterialInterface.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"

static TAutoConsoleVariable<int32> CVarTrafficDynamic(TEXT("berat.TrafficDynamic"), 1,
	TEXT("Traffic near the player as physics bodies (1) or always kinematic (0)."));

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
		UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
		UMaterialInterface* White = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Berat/FX/M_LampWhite.M_LampWhite"));
		UMaterialInterface* Red = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Berat/FX/M_LampRed.M_LampRed"));
		for (int32 k = 0; k < 4; ++k)
		{
			UStaticMeshComponent* L = NewObject<UStaticMeshComponent>(this);
			L->SetStaticMesh(Cube);
			L->SetMaterial(0, k < 2 ? White : Red);
			L->SetMobility(EComponentMobility::Movable);
			L->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			L->SetCastShadow(false);
			L->SetupAttachment(B);
			L->SetVisibility(false);
			L->RegisterComponent();
			Cars[i].Lamps.Add(L);
		}
	}
	for (int32 k = 0; k < 12; ++k)
	{
		USpotLightComponent* S = NewObject<USpotLightComponent>(this);
		S->SetMobility(EComponentMobility::Movable);
		S->SetIntensityUnits(ELightUnits::Candelas);
		S->SetIntensity(5000.f);
		S->SetAttenuationRadius(4000.f);
		S->SetOuterConeAngle(32.f);
		S->SetInnerConeAngle(18.f);
		S->SetLightColor(FLinearColor(1.f, 0.93f, 0.82f));
		S->SetCastShadows(false);
		S->SetupAttachment(RootComponent);
		S->SetVisibility(false);
		S->RegisterComponent();
		Beams.Add(S);
	}
}

void ABeratTraffic::FitLamps(FBeratTrafficCar& C)
{
	// lamps at the body's front and rear corners, from the model's bounds (pivot at the road, X forward)
	const UStaticMesh* M = C.Body->GetStaticMesh();
	if (!M || C.Lamps.Num() < 4)
	{
		return;
	}
	const FBoxSphereBounds B = M->GetBounds();
	const FVector Lo = B.Origin - B.BoxExtent, Hi = B.Origin + B.BoxExtent;
	const float Z = Lo.Z + (Hi.Z - Lo.Z) * 0.42f, Y = B.BoxExtent.Y * 0.58f;   // bounds include the mirrors
	const FVector Size(0.03f, 0.17f, 0.07f);        // the cube is 1 m
	C.Lamps[0]->SetRelativeLocation(FVector(Hi.X - 12.f, -Y, Z));
	C.Lamps[1]->SetRelativeLocation(FVector(Hi.X - 12.f, Y, Z));
	C.Lamps[2]->SetRelativeLocation(FVector(Lo.X + 4.f, -Y, Z + 8.f));
	C.Lamps[3]->SetRelativeLocation(FVector(Lo.X + 4.f, Y, Z + 8.f));
	for (UStaticMeshComponent* L : C.Lamps)
	{
		L->SetRelativeScale3D(Size);
	}
}

void ABeratTraffic::UpdateLamps(float Dt)
{
	LampClock += Dt;
	if (LampClock < 0.2f)
	{
		return;
	}
	LampClock = 0.f;
	const bool bOn = ABeratTimeOfDay::GetNightFactor(this) > 0.25f;
	FVector Eye = FVector::ZeroVector;
	if (APlayerController* PC = GetWorld()->GetFirstPlayerController())
	{
		FRotator R;
		PC->GetPlayerViewPoint(Eye, R);
	}
	// lamps of every active car; beams on the nearest ones within 150 m
	TArray<TPair<float, int32>> Near;
	for (int32 i = 0; i < Cars.Num(); ++i)
	{
		FBeratTrafficCar& C = Cars[i];
		const bool bShow = bOn && C.bActive;
		for (UStaticMeshComponent* L : C.Lamps)
		{
			if (L->IsVisible() != bShow)
			{
				L->SetVisibility(bShow);
			}
		}
		if (bShow)
		{
			const float D = FVector::Dist(C.Body->GetComponentLocation(), Eye);
			if (D < 15000.f)
			{
				Near.Add({D, i});
			}
		}
	}
	Near.Sort([](const TPair<float, int32>& A, const TPair<float, int32>& B) { return A.Key < B.Key; });
	for (int32 k = 0; k < Beams.Num(); ++k)
	{
		USpotLightComponent* S = Beams[k];
		if (k < Near.Num())
		{
			const FBeratTrafficCar& C = Cars[Near[k].Value];
			const FVector Front = (C.Lamps[0]->GetComponentLocation() + C.Lamps[1]->GetComponentLocation()) * 0.5f;
			FRotator R = C.Body->GetComponentRotation();
			R.Pitch -= 6.f;
			S->SetWorldLocationAndRotation(Front + C.Body->GetForwardVector() * 10.f, R);
			S->SetVisibility(true);
		}
		else if (S->IsVisible())
		{
			S->SetVisibility(false);
		}
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
		PlayerPos = PC->GetPawn() ? PC->GetPawn()->GetActorLocation() : ViewPos;
	}

	// wrecks: free physics; back to the pool when far and out of sight (or after a minute)
	for (FBeratTrafficCar& C : Cars)
	{
		if (!C.bWrecked)
		{
			continue;
		}
		C.WreckTime += Dt;
		const FVector At = C.Body->GetComponentLocation();
		const bool bSeen = FVector::DotProduct((At - ViewPos).GetSafeNormal(), ViewDir) > 0.3f;
		if ((C.WreckTime > 20.f && FVector::Dist(At, PlayerPos) > 8000.f && !bSeen) || C.WreckTime > 60.f || At.Z < -100000.f)
		{
			Release(C);
		}
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
		else if (!C.bWrecked)
		{
			if (C.bDynamic)
			{
				SetDynamic(C, false, FVector::ZeroVector);
			}
			C.Body->SetVisibility(false);
			C.Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		}
	}

	UpdateLamps(Dt);
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
	const float Near = FVector::Dist(Mid, PlayerPos);
	if (!C.bDynamic && Near < DynamicRadius && CVarTrafficDynamic.GetValueOnGameThread() != 0)
	{
		C.Body->SetWorldLocationAndRotation(Mid, Dir.Rotation(), false, nullptr, ETeleportType::TeleportPhysics);
		// a physics body only over loaded ground (a car made dynamic where the ground had not streamed in fell forever)
		FHitResult Hit;
		FCollisionQueryParams Q(NAME_None, false, this);
		if (GetWorld()->LineTraceSingleByChannel(Hit, Mid + FVector(0, 0, 200.f), Mid - FVector(0, 0, 500.f), ECC_Visibility, Q))
		{
			SetDynamic(C, true, Dir * C.Speed);
		}
		return;
	}
	if (C.bDynamic)
	{
		const FVector At = C.Body->GetComponentLocation();
		const FVector Up = C.Body->GetUpVector();
		// knocked off its line or tipped: a wreck (free physics, out of the lane bookkeeping)
		const FVector Now = C.Body->GetPhysicsLinearVelocity();
		const bool bImpact = !C.LastSetVelocity.IsZero() && FVector::Dist2D(Now, C.LastSetVelocity) > 250.f;
		if (bImpact || FVector::Dist2D(At, Mid) > 180.f || Up.Z < 0.85f)
		{
			C.bWrecked = true;
			C.bActive = false;
			C.WreckTime = 0.f;
			return;
		}
		if (Near > DynamicRadius * 1.4f || At.Z < Mid.Z - 300.f)      // far again, or sinking below its lane
		{
			SetDynamic(C, false, FVector::ZeroVector);
			C.Body->SetWorldLocationAndRotation(Mid, Dir.Rotation(), false, nullptr, ETeleportType::TeleportPhysics);
			return;
		}
		// steered along the lane by velocity: lane speed plus a pull back onto the line; yaw turned to the lane heading
		const FVector V = C.Body->GetPhysicsLinearVelocity();
		FVector Want = Dir * C.Speed + (Mid - At) * 3.f;
		Want.Z = V.Z;
		C.Body->SetPhysicsLinearVelocity(Want);
		C.LastSetVelocity = Want;
		const float Yaw = FRotator::NormalizeAxis(Dir.Rotation().Yaw - C.Body->GetComponentRotation().Yaw);
		FVector W = C.Body->GetPhysicsAngularVelocityInDegrees();
		W.Z = FMath::Clamp(Yaw * 4.f, -90.f, 90.f);
		C.Body->SetPhysicsAngularVelocityInDegrees(W);
		return;
	}
	C.Body->SetWorldLocationAndRotation(Mid, Dir.Rotation(), false, nullptr, ETeleportType::TeleportPhysics);
}

void ABeratTraffic::SetDynamic(FBeratTrafficCar& C, bool bOn, const FVector& Velocity)
{
	C.bDynamic = bOn;
	C.LastSetVelocity = FVector::ZeroVector;
	C.Body->SetSimulatePhysics(bOn);
	if (bOn)
	{
		// mass from the model's length: 4 m car ~1250 kg, 7 m van ~3500 kg
		const float Mass = FMath::Clamp(C.Length / 100.f * 310.f, 900.f, 4000.f);
		C.Body->SetMassOverrideInKg(NAME_None, Mass, true);
		C.Body->SetLinearDamping(0.05f);
		C.Body->SetAngularDamping(0.4f);
		C.Body->SetPhysicsLinearVelocity(Velocity);
	}
}

void ABeratTraffic::Release(FBeratTrafficCar& C)
{
	C.bWrecked = false;
	C.bActive = false;
	SetDynamic(C, false, FVector::ZeroVector);
	C.Body->SetVisibility(false);
	C.Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	for (UStaticMeshComponent* L : C.Lamps)
	{
		L->SetVisibility(false);
	}
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
		if (!Cars[i].bActive && !Cars[i].bWrecked)
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
	FitLamps(C);
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


bool ABeratTraffic::NearestCar(const FVector& From, FVector& OutPos, FVector& OutDir, float& OutSpeed) const
{
	float Best = 1e18f;
	for (const FBeratTrafficCar& C : Cars)
	{
		if (!C.bActive)
		{
			continue;
		}
		const float D = FVector::DistSquared(C.Body->GetComponentLocation(), From);
		if (D < Best)
		{
			Best = D;
			OutPos = C.Body->GetComponentLocation();
			OutDir = C.Body->GetForwardVector();
			OutSpeed = C.Speed;
		}
	}
	return Best < 1e18f;
}

int32 ABeratTraffic::NumWrecked() const
{
	int32 N = 0;
	for (const FBeratTrafficCar& C : Cars)
	{
		N += C.bWrecked;
	}
	return N;
}

int32 ABeratTraffic::NumDynamic() const
{
	int32 N = 0;
	for (const FBeratTrafficCar& C : Cars)
	{
		N += C.bDynamic && !C.bWrecked;
	}
	return N;
}

void ABeratTraffic::GetCarPositions(TArray<FVector>& Out) const
{
	for (const FBeratTrafficCar& C : Cars)
	{
		if (C.bActive || C.bWrecked)
		{
			Out.Add(C.Body->GetComponentLocation());
		}
	}
}
