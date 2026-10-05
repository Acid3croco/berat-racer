#include "BeratGameMode.h"

#include "BeratCar.h"
#include "BeratTimeOfDay.h"
#include "BeratLaneGraph.h"
#include "BeratTraffic.h"
#include "ChaosVehicleMovementComponent.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "ChaosVehicleWheel.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerStart.h"
#include "HighResScreenshot.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonReader.h"
#include "CanvasItem.h"
#include "Engine/Texture2D.h"
#include "Components/DecalComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Engine/OverlapResult.h"
#include "Engine/StaticMeshActor.h"
#include "PhysicsEngine/BodySetup.h"
#include "UnrealClient.h"
#include "Misc/Paths.h"

ABeratGameMode::ABeratGameMode()
{
	PlayerControllerClass = ABeratPlayerController::StaticClass();
	HUDClass = ABeratHUD::StaticClass();
	DefaultPawnClass = nullptr;
}

UClass* ABeratGameMode::GetDefaultPawnClassForController_Implementation(AController* InController)
{
	int32 Pick = 0;
	if (FParse::Value(FCommandLine::Get(), TEXT("BeratCar="), Pick) && Cars.IsValidIndex(Pick))
	{
		Current = Pick;   // -BeratCar=N: start with the N-th car of the garage
	}
	if (Cars.IsValidIndex(Current) && Cars[Current])
	{
		return Cars[Current];
	}
	return Super::GetDefaultPawnClassForController_Implementation(InController);
}

APawn* ABeratGameMode::SpawnDefaultPawnFor_Implementation(AController* NewPlayer, AActor* StartSpot)
{
	if (Cars.Num() == 0 || !Cars[Current])
	{
		return Super::SpawnDefaultPawnFor_Implementation(NewPlayer, StartSpot);
	}
	FActorSpawnParameters P;
	P.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;
	P.Instigator = GetInstigator();
	const FTransform T = StartSpot ? StartSpot->GetActorTransform() : FTransform::Identity;
	ABeratCar* Car = GetWorld()->SpawnActor<ABeratCar>(Cars[Current], T.GetLocation() + FVector(0, 0, 60.f), T.Rotator(), P);
	UE_LOG(LogTemp, Display, TEXT("[berat] spawned %s at %s (start %s)"), Car ? *Car->GetName() : TEXT("nothing"),
		*T.GetLocation().ToString(), StartSpot ? *StartSpot->GetName() : TEXT("none"));
	return Car;
}

void ABeratGameMode::NextCar(APlayerController* PC)
{
	if (Cars.Num() < 2 || !PC)
	{
		return;
	}
	APawn* Old = PC->GetPawn();
	FVector At = Old ? Old->GetActorLocation() : FVector::ZeroVector;
	FRotator Rot = Old ? FRotator(0, Old->GetActorRotation().Yaw, 0) : FRotator::ZeroRotator;
	const FVector Vel = Old ? Old->GetVelocity() : FVector::ZeroVector;
	Current = (Current + 1) % Cars.Num();
	if (Old)
	{
		PC->UnPossess();
		Old->Destroy();
	}
	FActorSpawnParameters P;
	P.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;
	ABeratCar* Car = GetWorld()->SpawnActor<ABeratCar>(Cars[Current], At + FVector(0, 0, 80.f), Rot, P);
	if (Car)
	{
		PC->Possess(Car);
		Car->GetMesh()->SetPhysicsLinearVelocity(Vel);
	}
}

ABeratPlayerController::ABeratPlayerController()
{
	bAutoManageActiveCameraTarget = true;
	PrimaryActorTick.bCanEverTick = true;
}

void ABeratPlayerController::BeginPlay()
{
	Super::BeginPlay();
	bTest = FParse::Param(FCommandLine::Get(), TEXT("BeratTest"));
}

void ABeratPlayerController::TestShot(const FString& Name)
{
	FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir() / TEXT("Shots") / (Name + TEXT(".png")), false, false);
}

void ABeratPlayerController::TestReport(const TCHAR* Phase)
{
	if (FrameMs.Num() == 0)
	{
		return;
	}
	TArray<float> S = FrameMs;
	S.Sort();
	double Sum = 0;
	for (float F : S)
	{
		Sum += F;
	}
	const float Avg = Sum / S.Num();
	const float P99 = S[FMath::Min(S.Num() - 1, int32(S.Num() * 0.99f))];
	const ABeratCar* Car = Cast<ABeratCar>(GetPawn());
	const FVector P = Car ? Car->GetActorLocation() : FVector::ZeroVector;
	if (RideFrames > 1)
	{
		UE_LOG(LogTemp, Display, TEXT("[berat-test]   ride: vertical acceleration rms %.2f m/s2, %d jolts (> 0.5 m/s in a frame) in %d frames"),
			FMath::Sqrt(RideSq / RideFrames) / 100.f, RideJolts, RideFrames);
		RideSq = 0.f; RideJolts = 0; RideFrames = 0;
	}
	UE_LOG(LogTemp, Display, TEXT("[berat-test] %s: %d frames, avg %.2f ms (%.1f fps), p99 %.2f ms (%.1f fps), worst %.2f ms; speed %.1f km/h, gear %d, at x %.1f y %.1f z %.2f m"),
		Phase, S.Num(), Avg, 1000.f / Avg, P99, 1000.f / P99, S.Last(), Car ? Car->GetSpeedKmh() : 0.f, Car ? Car->GetGear() : 0,
		P.X / 100.0, -P.Y / 100.0, P.Z / 100.0);
	FrameMs.Reset();
	for (TActorIterator<ABeratTraffic> It(GetWorld()); It; ++It)
	{
		UE_LOG(LogTemp, Display, TEXT("[berat-test]   traffic: %d cars active"), It->NumActive());
	}
	if (Car)
	{
		UChaosWheeledVehicleMovementComponent* W = Cast<UChaosWheeledVehicleMovementComponent>(Car->GetVehicleMovementComponent());
		FVector CamLoc; FRotator CamRot;
		GetPlayerViewPoint(CamLoc, CamRot);
		const FBoxSphereBounds B = Car->GetMesh()->Bounds;
		UE_LOG(LogTemp, Display, TEXT("[berat-test]   car mesh %s, simulating %d, bounds extent %s, camera %.1f m from the car; wheels %d, rpm %.0f, throttle in %.2f, mass %.0f kg"),
			*GetNameSafe(Car->GetMesh()->GetSkeletalMeshAsset()), Car->GetMesh()->IsSimulatingPhysics(), *B.BoxExtent.ToString(),
			FVector::Dist(CamLoc, Car->GetActorLocation()) / 100.0, W ? W->GetNumWheels() : -1, Car->GetRpm(),
			Car->GetVehicleMovementComponent()->GetThrottleInput(), Car->GetMesh()->GetMass());
		for (int32 i = 0; W && i < W->GetNumWheels(); ++i)
		{
			const FWheelStatus& WS = W->GetWheelState(i);
			UE_LOG(LogTemp, Display, TEXT("[berat-test]   wheel %d: contact %d (%s), suspension %.2f, drive %.0f N.m, brake %.0f N.m, slip %.2f, skid %d"),
				i, WS.bInContact, *GetNameSafe(WS.PhysMaterial.Get()), WS.NormalizedSuspensionLength, WS.DriveTorque, WS.BrakeTorque,
				WS.SlipMagnitude, WS.bIsSkidding);
		}
		UE_LOG(LogTemp, Display, TEXT("[berat-test]   wheel puffs %d, track decals %d"), Car->WheelFxSpawned, Car->WheelTracksSpawned);
		UE_LOG(LogTemp, Display, TEXT("[berat-test]   handbrake %.2f, brake %.2f, steering %.2f, parked %d"),
			W ? W->GetHandbrakeInput() : -1.f, W ? W->GetBrakeInput() : -1.f, W ? W->GetSteeringInput() : -1.f, W ? W->IsParked() : -1);
	}
}

void ABeratPlayerController::Pilot(ABeratCar* Car, float Dt)
{
	const UBeratLaneGraph* G = nullptr;
	for (TActorIterator<ABeratTraffic> It(GetWorld()); It; ++It)
	{
		G = It->Graph;
	}
	if (!G || !Car)
	{
		return;
	}
	const FVector P = Car->GetActorLocation();
	FVector At, Dir;
	if (PilotLane == INDEX_NONE)
	{
		// the nearest point of a lane (kind lane) heading roughly where the car faces
		double Best = 1e18;
		for (int32 L = 0; L < G->Lanes.Num(); ++L)
		{
			const FBeratLane& Lane = G->Lanes[L];
			if (Lane.Kind != EBeratLaneKind::Lane)
			{
				continue;
			}
			for (int32 i = 0; i + 1 < Lane.Points.Num(); ++i)
			{
				const double D2 = FVector::DistSquared(Lane.Points[i], P);
				const FVector LD = (Lane.Points[i + 1] - Lane.Points[i]).GetSafeNormal();
				if (D2 < Best && FVector::DotProduct(LD, Car->GetActorForwardVector()) > 0.5f)
				{
					Best = D2;
					PilotLane = L;
					PilotS = Lane.Distance[i];
				}
			}
		}
		if (PilotLane == INDEX_NONE)
		{
			return;
		}
		UE_LOG(LogTemp, Display, TEXT("[berat-test] autopilot on lane %lld, %.1f m from the car"), G->Lanes[PilotLane].Id, FMath::Sqrt(Best) / 100.0);
	}
	// the straightest continuation of a lane, no U-turns
	auto PickNext = [G](int32 Lane) -> int32
	{
		const TArray<int32>& Next = G->Lanes[Lane].Next;
		int32 Pick = Next.Num() ? Next[0] : INDEX_NONE;
		float Straight = -1.f;
		for (int32 N : Next)
		{
			const FBeratLane& NL = G->Lanes[N];
			const float Score = NL.Kind == EBeratLaneKind::UTurn ? -2.f : -FMath::Abs(NL.Turn);
			if (Straight < -0.5f || Score > Straight) { Straight = Score; Pick = N; }
		}
		return Pick;
	};
	// advance the reference point to the car's projection on the lane, moving to the next lane at its end
	for (int32 Guard = 0; Guard < 8; ++Guard)
	{
		G->Sample(PilotLane, PilotS, At, Dir);
		if (FVector::DotProduct(P - At, Dir) <= 0.f)
		{
			break;
		}
		PilotS += FMath::Max(FVector::DotProduct(P - At, Dir), 50.f);
		if (PilotS > G->Lanes[PilotLane].Length())
		{
			const TArray<int32>& Next = G->Lanes[PilotLane].Next;
			if (Next.Num() == 0)
			{
				break;
			}
			PilotS -= G->Lanes[PilotLane].Length();
			PilotLane = PickNext(PilotLane);
		}
	}
	const float Speed = FMath::Abs(Car->GetSpeedKmh());
	const float Look = 600.f + Speed * 25.f;                    // cm ahead: 6 m + 0.9 s at speed
	FVector Target, TDir;
	G->Sample(PilotLane, PilotS + Look, Target, TDir);
	const FVector Local = Car->GetActorTransform().InverseTransformPosition(Target);
	const float Steer = FMath::Clamp(FMath::Atan2(Local.Y, Local.X) * 2.2f, -1.f, 1.f);
	// target speed: the lane's own (curvature and limit), capped
	// target speed: the slowest the lanes ahead allow (curvature, limit) over 40 m, on the continuation the pilot will
	// take, less what braking at 4 m/s2 sheds on the way: v = sqrt(v_ahead^2 + 2 a d)
	float WantMs = PilotKmh / 3.6f;
	{
		int32 Ln = PilotLane;
		float S0 = PilotS, Ahead = 0.f;
		for (int32 Hop = 0; Hop < 4 && Ln != INDEX_NONE && Ahead < 4000.f; ++Hop)
		{
			const FBeratLane& LL = G->Lanes[Ln];
			for (int32 k = 0; k < LL.Points.Num() && k < LL.Speed.Num(); ++k)
			{
				const float D = LL.Distance[k] - S0;
				if (D < 0.f)
				{
					continue;
				}
				const float Dm = (Ahead + D) / 100.f;
				if (Dm > 40.f)
				{
					break;
				}
				WantMs = FMath::Min(WantMs, FMath::Sqrt(FMath::Square(LL.Speed[k]) + 8.f * Dm));
			}
			Ahead += LL.Length() - S0;
			S0 = 0.f;
			Ln = PickNext(Ln);
		}
	}
	const float Want = WantMs * 3.6f;
	const FBeratLane& L = G->Lanes[PilotLane];
	Car->AutoSteer = Steer;
	static bool bDumped = false;
	if (!bDumped && Speed < 1.f && Car->AutoThrottle > 0.5f && GetWorld()->GetTimeSeconds() > 12.0)
	{
		bDumped = true;
		TArray<FOverlapResult> Over;
		FCollisionQueryParams Q(TEXT("BeratStuck"), true, Car);
		GetWorld()->OverlapMultiByObjectType(Over, P + FVector(0, 0, 60), FQuat::Identity, FCollisionObjectQueryParams::AllObjects,
			FCollisionShape::MakeBox(FVector(320, 320, 120)), Q);
		for (const FOverlapResult& O : Over)
		{
			UE_LOG(LogTemp, Display, TEXT("[berat-stuck] overlaps %s / %s (%s), profile %s"), *GetNameSafe(O.GetActor()),
				*GetNameSafe(O.GetComponent()), O.GetComponent() ? *O.GetComponent()->GetClass()->GetName() : TEXT("-"),
				O.GetComponent() ? *O.GetComponent()->GetCollisionProfileName().ToString() : TEXT("-"));
		}
		for (int32 dy = -4; dy <= 4; ++dy)
		{
			const FVector S = P + FVector(0, dy * 100.f, 300.f), E = P + FVector(0, dy * 100.f, -300.f);
			TArray<FHitResult> Hits;
			FCollisionQueryParams TQ(TEXT("BeratStuckTrace"), true, Car);
			GetWorld()->LineTraceMultiByObjectType(Hits, S, E, FCollisionObjectQueryParams::AllObjects, TQ);
			FString Line;
			for (const FHitResult& H : Hits) { Line += FString::Printf(TEXT(" %s %.3f;"), *GetNameSafe(H.GetActor()), H.ImpactPoint.Z / 100.0); }
			UE_LOG(LogTemp, Display, TEXT("[berat-stuck] trace y %+d m:%s"), -dy, *Line);
		}
	}
	static int32 ShotN = 0;
	const double Now = GetWorld()->GetTimeSeconds();
	if (Now > 14.5 && Now < 16.6 && Now > 14.5 + ShotN * 0.25)
	{
		TestShot(FString::Printf(TEXT("seq_%02d"), ShotN++));
	}
	static double LastLog = 0.0;
	if (GetWorld()->GetTimeSeconds() - LastLog > 1.0)
	{
		LastLog = GetWorld()->GetTimeSeconds();
		const FVector V = Car->GetVelocity();
		UE_LOG(LogTemp, Display, TEXT("[berat-pilot] t %.0f s: car (%.1f, %.1f, %.2f) yaw %.0f, %.1f km/h (velocity %.1f km/h), target (%.1f, %.1f) local (%.1f, %.1f) m, steer %.2f, lane %lld s %.0f m, want %.0f km/h"),
			LastLog, P.X / 100.0, -P.Y / 100.0, P.Z / 100.0, Car->GetActorRotation().Yaw, Speed, V.Size() * 0.036,
			Target.X / 100.0, -Target.Y / 100.0, Local.X / 100.0, Local.Y / 100.0, Steer, L.Id, PilotS / 100.0, Want);
	}
	Car->AutoThrottle = Speed < Want ? FMath::Clamp((Want - Speed) / 10.f, 0.f, 1.f) : 0.f;
	Car->AutoBrake = Speed > Want + 3.f ? FMath::Clamp((Speed - Want) / 12.f, 0.f, 1.f) : 0.f;
}

void ABeratPlayerController::Tick(float Dt)
{
	Super::Tick(Dt);
	if (!bTest)
	{
		return;
	}
	TestClock += Dt;
	if (TestClock > 3.f)
	{
		FrameMs.Add(Dt * 1000.f);
	}
	if (const ABeratCar* RC = Cast<ABeratCar>(GetPawn()); RC && RC->GetMesh()->IsSimulatingPhysics() && TestClock > 8.f && Dt > 0.f)
	{
		const float Vz = RC->GetMesh()->GetPhysicsLinearVelocity().Z;
		if (RideFrames > 0)
		{
			const float Az = (Vz - RideVz) / Dt;
			RideSq += Az * Az;
			RideJolts += FMath::Abs(Vz - RideVz) > 50.f;
		}
		RideVz = Vz;
		++RideFrames;
	}
	ABeratCar* Car = Cast<ABeratCar>(GetPawn());
	if (FParse::Param(FCommandLine::Get(), TEXT("BeratHandling")))
	{
		Handling(Car, Dt);
		return;
	}
	// -BeratCrash: at 12 s, 25 m behind the nearest traffic car on its line, 30 km/h faster, throttle on: a rear-end hit
	if (Car && FParse::Param(FCommandLine::Get(), TEXT("BeratCrash")))
	{
		ABeratTraffic* Tr = nullptr;
		for (TActorIterator<ABeratTraffic> It(GetWorld()); It; ++It) { Tr = *It; }
		static int32 Phase = 0;
		static float Before = 0.f, Clock = 0.f;
		FVector P, D; float V;
		if (Phase == 0 && TestClock > 12.f && Tr && Tr->NearestCar(Car->GetActorLocation(), P, D, V))
		{
			Car->SetActorLocationAndRotation(P - D * 1200.f + FVector(0, 0, 60.f), D.Rotation(), false, nullptr, ETeleportType::TeleportPhysics);
			Car->GetMesh()->SetPhysicsLinearVelocity(D * (V + 830.f));
			Car->AutoThrottle = 1.f; Car->AutoSteer = 0.f; Car->AutoBrake = 0.f;
			UE_LOG(LogTemp, Display, TEXT("[berat-crash] 12 m behind a traffic car at %.0f km/h, closing at 30 km/h"), V * 0.036f);
			Phase = 1; Clock = 0.f;
		}
		else if (Phase == 1)
		{
			Clock += Dt;
			if (Clock < 0.3f) { Before = Car->GetSpeedKmh(); }
			if (Tr && Tr->NearestCar(Car->GetActorLocation(), P, D, V))      // aim at it
			{
				const FVector L = Car->GetActorTransform().InverseTransformPosition(P);
				Car->AutoSteer = FMath::Clamp(FMath::Atan2(L.Y, L.X) * 3.f, -1.f, 1.f);
			}
			if (Clock > 1.0f && Clock < 1.0f + Dt * 1.5f || Clock > 4.f)
			{
				UE_LOG(LogTemp, Display, TEXT("[berat-crash] t+%.1f s: player %.0f km/h (was %.0f), traffic dynamic %d, wrecked %d"),
					Clock, Car->GetSpeedKmh(), Before, Tr ? Tr->NumDynamic() : -1, Tr ? Tr->NumWrecked() : -1);
				if (Clock > 4.f) { TestShot(TEXT("crash")); Phase = 2; }
			}
		}
		else if (Phase == 2)
		{
			Car->AutoThrottle = 0.f; Car->AutoBrake = 1.f;
			Clock += Dt;
			if (Clock > 5.f) { FGenericPlatformMisc::RequestExit(false); }
		}
		return;
	}
	// -BeratStart=x,y (package metres): put the car on the nearest lane there, along it, once.
	// -BeratOffroad=x,y,heading (degrees from east, counter-clockwise): exactly there on the ground, then straight ahead on
	// part throttle instead of the autopilot (grip and drag per surface).
	FString StartArg, OffroadArg;
	const bool bOffroad = FParse::Value(FCommandLine::Get(), TEXT("BeratOffroad="), OffroadArg, false);
	// (the spot may lie outside the cells streamed around the spawn: the car waits there, physics off, until the ground loads)
	if (Car && TestStep == 0 && TestClock > 1.f && !bMoved && bOffroad)
	{
		TArray<FString> V;
		OffroadArg.ParseIntoArray(V, TEXT(","));
		const double X = V.Num() > 0 ? FCString::Atod(*V[0]) * 100.0 : 0.0, Y = V.Num() > 1 ? -FCString::Atod(*V[1]) * 100.0 : 0.0;
		const float Heading = V.Num() > 2 ? FCString::Atof(*V[2]) : 0.f;
		FHitResult Hit;
		FCollisionQueryParams Q(NAME_None, false, Car);
		if (GetWorld()->LineTraceSingleByChannel(Hit, FVector(X, Y, 1e6), FVector(X, Y, -1e5), ECC_Visibility, Q))
		{
			bMoved = true;
			Car->SetActorLocationAndRotation(Hit.ImpactPoint + FVector(0, 0, 80.f), FRotator(0, -Heading, 0), false, nullptr, ETeleportType::TeleportPhysics);
			Car->GetMesh()->SetSimulatePhysics(true);
			Car->GetMesh()->SetPhysicsLinearVelocity(FVector::ZeroVector);
			TestClock = 1.f;
			UE_LOG(LogTemp, Display, TEXT("[berat-test] off-road start at (%.1f, %.1f, %.2f) heading %.0f"), X / 100.0, -Y / 100.0, Hit.ImpactPoint.Z / 100.0, Heading);
		}
		else if (Car->GetMesh()->IsSimulatingPhysics())
		{
			Car->GetMesh()->SetSimulatePhysics(false);
			Car->SetActorLocation(FVector(X, Y, Car->GetActorLocation().Z + 2000.f), false, nullptr, ETeleportType::TeleportPhysics);
		}
		else
		{
			TestClock = 1.f + Dt;     // hold the test clock until the start is placed
		}
	}
	if (Car && TestStep == 0 && TestClock > 1.f && !bMoved && FParse::Value(FCommandLine::Get(), TEXT("BeratStart="), StartArg, false))
	{
		bMoved = true;
		FString Xs, Ys;
		StartArg.Split(TEXT(","), &Xs, &Ys);
		const FVector Want(FCString::Atod(*Xs) * 100.0, -FCString::Atod(*Ys) * 100.0, 0.0);
		for (TActorIterator<ABeratTraffic> It(GetWorld()); It; ++It)
		{
			if (const UBeratLaneGraph* G = It->Graph)
			{
				double Best = 1e18; FVector P, D;
				for (const FBeratLane& L : G->Lanes)
				{
					if (L.Kind != EBeratLaneKind::Lane) continue;
					for (int32 i = 0; i + 1 < L.Points.Num(); ++i)
					{
						const double D2 = FVector::DistSquared2D(L.Points[i], Want);
						if (D2 < Best) { Best = D2; P = L.Points[i]; D = L.Points[i + 1] - L.Points[i]; }
					}
				}
				Car->GetMesh()->SetPhysicsLinearVelocity(FVector::ZeroVector);
				Car->SetActorLocationAndRotation(P + FVector(0, 0, 80.f), FRotator(0, D.Rotation().Yaw, 0), false, nullptr, ETeleportType::TeleportPhysics);
				UE_LOG(LogTemp, Display, TEXT("[berat-test] start moved to (%.1f, %.1f, %.2f)"), P.X / 100.0, -P.Y / 100.0, P.Z / 100.0);
			}
		}
	}
	// -BeratDecalTest: one large track decal 6 m ahead of the car (decal rendering check in the start shot)
	if (Car && (bMoved || TestClock > 2.5f) && !bDecalTested && TestClock > 2.0f && FParse::Param(FCommandLine::Get(), TEXT("BeratDecalTest")))
	{
		bDecalTested = true;
		const FVector F = Car->GetActorForwardVector().GetSafeNormal2D();
		const FVector At = Car->GetActorLocation();
		UE_LOG(LogTemp, Display, TEXT("[berat-test] car forward %s, velocity dir n/a, camera at %s"), *F.ToString(), *PlayerCameraManager->GetCameraLocation().ToString());
		FString Which;
		FParse::Value(FCommandLine::Get(), TEXT("BeratDecalMat="), Which, false);
		UMaterialInterface* M = LoadObject<UMaterialInterface>(nullptr, Which.IsEmpty() ? TEXT("/Game/Berat/FX/M_TyreTrack.M_TyreTrack") : *Which);
		UDecalComponent* D = UGameplayStatics::SpawnDecalAtLocation(this, M, FVector(200.f, 400.f, 400.f), At,
			FRotationMatrix::MakeFromXY(FVector(0, 0, -1), F).Rotator(), 60.f);
		UE_LOG(LogTemp, Display, TEXT("[berat-test] decal test %s at %s, material [%s] -> %s"), D ? TEXT("spawned") : TEXT("FAILED"), *At.ToString(), *Which, *GetNameSafe(M));
	}
	// the full map, once (shot at 45.5 s, closed at 46.5 s)
	static int32 MapShot = 0;
	if (MapShot == 0 && TestClock > 45.f) { if (ABeratHUD* H = Cast<ABeratHUD>(GetHUD())) { H->ToggleMap(); } MapShot = 1; }
	else if (MapShot == 1 && TestClock > 45.5f) { TestShot(TEXT("map")); MapShot = 2; }
	else if (MapShot == 2 && TestClock > 46.5f) { if (ABeratHUD* H = Cast<ABeratHUD>(GetHUD())) { H->ToggleMap(); } MapShot = 3; }
	if (TestStep >= 2 && Car)
	{
		if (bOffroad)
		{
			// straight on part throttle, then a stop and a still shot of what the wheels left (tracks, settling dust)
			const bool bStop = TestClock > 26.f;
			Car->AutoThrottle = bStop ? 0.f : 0.6f; Car->AutoSteer = 0.f; Car->AutoBrake = bStop && Car->GetSpeedKmh() > 3.f ? 1.f : 0.f;   // released near 0: brake held there selects reverse
			if (TestClock > 28.5f)
			{
				Car->SetTestLook(FVector2D(1.0, 0.9));        // look back down the trail
			}
			if (TestClock > 31.f && !bOffroadShot)
			{
				bOffroadShot = true;
				TestShot(TEXT("offroad_stopped"));
			}
		}
		else
		{
			Pilot(Car, Dt);
		}
	}
	// 0-3 s settle; 3-8 s parked (frame time standing); 8-48 s autopilot on the lane graph; reports and shots on the way.
	const float Marks[] = {3.f, 8.f, 20.f, 30.f, 48.f, 49.f};
	if (TestStep < UE_ARRAY_COUNT(Marks) && TestClock >= Marks[TestStep])
	{
		switch (TestStep)
		{
		case 0:
			FrameMs.Reset(); TestShot(TEXT("game_start"));
			for (TActorIterator<AStaticMeshActor> It(GetWorld()); It; ++It)
			{
				const UStaticMeshComponent* C = It->GetStaticMeshComponent();
				const UStaticMesh* M = C ? C->GetStaticMesh() : nullptr;
				if (!M || !M->GetName().StartsWith(TEXT("SM_roads")))
				{
					continue;
				}
				const UBodySetup* B = M->GetBodySetup();
				UE_LOG(LogTemp, Display, TEXT("[berat-test] loaded %s: collision %d, trace flag %d, trimeshes %d, physics state %d"),
					*M->GetName(), int32(C->GetCollisionEnabled()), B ? int32(B->CollisionTraceFlag) : -1,
					B ? B->TriMeshGeometries.Num() : -1, C->IsPhysicsStateCreated());
			}
			break;
		case 1: TestReport(TEXT("parked")); ConsoleCommand(TEXT("CsvProfile Start")); break;
		case 2: TestShot(TEXT("game_drive")); break;
		case 3: TestReport(TEXT("driving 1")); break;
		case 4: TestReport(TEXT("driving 2")); TestShot(TEXT("game_drive2")); ConsoleCommand(TEXT("CsvProfile Stop")); break;
		case 5: FGenericPlatformMisc::RequestExit(false); break;
		}
		++TestStep;
	}
}

void ABeratPlayerController::SetupInputComponent()
{
	Super::SetupInputComponent();
	// Plain key bindings for the few menu-like actions; driving input lives on the car (Enhanced Input).
	InputComponent->BindKey(EKeys::M, IE_Pressed, this, &ABeratPlayerController::ToggleMap);
	InputComponent->BindKey(EKeys::Gamepad_Special_Right, IE_Pressed, this, &ABeratPlayerController::ToggleMap);
	InputComponent->BindKey(EKeys::Tab, IE_Pressed, this, &ABeratPlayerController::NextCar);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Right, IE_Pressed, this, &ABeratPlayerController::NextCar);
	InputComponent->BindKey(EKeys::T, IE_Pressed, this, &ABeratPlayerController::TimeForward);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Left, IE_Pressed, this, &ABeratPlayerController::TimeForward);
	InputComponent->BindKey(EKeys::Y, IE_Pressed, this, &ABeratPlayerController::TimeBack);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Down, IE_Pressed, this, &ABeratPlayerController::TimeBack);
}

void ABeratPlayerController::ToggleMap()
{
	if (ABeratHUD* H = Cast<ABeratHUD>(GetHUD()))
	{
		H->ToggleMap();
	}
}

void ABeratPlayerController::NextCar()
{
	if (ABeratGameMode* GM = GetWorld()->GetAuthGameMode<ABeratGameMode>())
	{
		GM->NextCar(this);
	}
}

ABeratTimeOfDay* ABeratPlayerController::Clock() const
{
	for (TActorIterator<ABeratTimeOfDay> It(GetWorld()); It; ++It)
	{
		return *It;
	}
	return nullptr;
}

void ABeratPlayerController::TimeForward()
{
	if (ABeratTimeOfDay* C = Clock())
	{
		C->SkipHours(1.f);
	}
}

void ABeratPlayerController::TimeBack()
{
	if (ABeratTimeOfDay* C = Clock())
	{
		C->SkipHours(-1.f);
	}
}

void ABeratHUD::DrawHUD()
{
	Super::DrawHUD();
	if (!Canvas)
	{
		return;
	}
	const float Dt = GetWorld()->GetDeltaSeconds();
	if (Dt > 0.f)
	{
		FpsSmooth = FMath::Lerp(FpsSmooth, 1.f / Dt, 0.05f);
	}
	UFont* Big = GEngine->GetLargeFont();
	UFont* Small = GEngine->GetSmallFont();
	const float X = Canvas->ClipX - 260.f, Y = Canvas->ClipY - 150.f;
	if (const ABeratCar* Car = Cast<ABeratCar>(GetOwningPawn()))
	{
		const int32 Gear = Car->GetGear();
		const FString GearText = Gear < 0 ? TEXT("R") : Gear == 0 ? TEXT("N") : FString::FromInt(Gear);
		DrawText(FString::Printf(TEXT("%3.0f km/h"), FMath::Abs(Car->GetSpeedKmh())), FLinearColor::White, X, Y, Big, 2.2f);
		DrawText(FString::Printf(TEXT("gear %s   %4.0f rpm"), *GearText, Car->GetRpm()), FLinearColor(0.85f, 0.85f, 0.85f), X, Y + 50.f, Small, 1.4f);
		DrawText(Car->DisplayName.ToString(), FLinearColor(1.f, 0.8f, 0.4f), X, Y + 75.f, Small, 1.4f);
	}
	for (TActorIterator<ABeratTimeOfDay> It(GetWorld()); It; ++It)
	{
		const float H = It->Hours;
		DrawText(FString::Printf(TEXT("%02d:%02d"), int32(H), int32(FMath::Fmod(H, 1.f) * 60.f)), FLinearColor::White, X, Y + 100.f, Small, 1.4f);
		break;
	}
	DrawText(FString::Printf(TEXT("%.0f fps"), FpsSmooth), FLinearColor(0.6f, 1.f, 0.6f), 20.f, 20.f, Small, 1.2f);
	// minimap (bottom left, ~500 m across, north up) or the full map (M)
	LoadMap();
	if (MapTex && GetOwningPawn())
	{
		const FVector P = GetOwningPawn()->GetActorLocation();
		const double U = (P.X / 100.0 - MapWest) / MapMpp / MapPixels, V = (MapNorth + P.Y / 100.0) / MapMpp / MapPixels;
		if (bMapOpen)
		{
			const float S = Canvas->ClipY * 0.86f;
			DrawRect(FLinearColor(0.f, 0.f, 0.f, 0.55f), 0.f, 0.f, Canvas->ClipX, Canvas->ClipY);
			DrawMap((Canvas->ClipX - S) * 0.5f, (Canvas->ClipY - S) * 0.5f, S, 0.5, 0.5, 1.0, true);
			DrawText(TEXT("Berat  -  9.6 x 9.6 km        M: close"), FLinearColor::White, (Canvas->ClipX - S) * 0.5f,
				(Canvas->ClipY - S) * 0.5f - 34.f, Small, 1.6f);
		}
		else
		{
			const float S = FMath::Min(300.f, Canvas->ClipY * 0.3f);
			DrawMap(24.f, Canvas->ClipY - S - 48.f, S, U, V, 500.0 / MapMpp / MapPixels, true);
		}
	}
	DrawText(TEXT("Tab / D-pad right: next car   T / Y: time +-1 h   L: lights   C: camera   R: reset   M: map"),
		FLinearColor(1.f, 1.f, 1.f, 0.6f), 20.f, Canvas->ClipY - 30.f, Small, 1.f);
}


void ABeratPlayerController::Handling(ABeratCar* Car, float Dt)
{
	if (!Car)
	{
		return;
	}
	UChaosWheeledVehicleMovementComponent* W = Cast<UChaosWheeledVehicleMovementComponent>(Car->GetVehicleMovementComponent());
	USkeletalMeshComponent* M = Car->GetMesh();
	const FVector Pad(0.0, 0.0, 300000.0);                 // the slab's top, 3 km up (out of the map)
	const float Kmh = Car->GetSpeedKmh();
	const FTransform T = M->GetComponentTransform();
	const FVector Fwd = T.GetUnitAxis(EAxis::X), Right = T.GetUnitAxis(EAxis::Y), Up = T.GetUnitAxis(EAxis::Z);
	const FVector V = M->GetPhysicsLinearVelocity();
	const float YawRate = M->GetPhysicsAngularVelocityInDegrees().Z;                          // deg/s
	const float Slip = FMath::RadiansToDegrees(FMath::Atan2(FVector::DotProduct(V, Right), FMath::Max(FVector::DotProduct(V, Fwd), 1.f)));
	const float LatG = FVector::DotProduct(V, Right) * 0.f + FMath::Abs(FMath::DegreesToRadians(YawRate) * V.Size() / 100.f) / 9.81f;
	const float Roll = FMath::RadiansToDegrees(FMath::Asin(FMath::Clamp(Right.Z, -1.f, 1.f)));
	HClock += Dt;
	auto Next = [this]() { ++HStep; HClock = 0.f; HMaxSlip = 0.f; HCount = 0; for (float& x : HSum) { x = 0.f; } };
	const FString NameS = Car->DisplayName.ToString();
	const TCHAR* Name = *NameS;
	switch (HStep)
	{
	case -1:   // the slab, the car on it
	{
		AStaticMeshActor* Slab = GetWorld()->SpawnActor<AStaticMeshActor>(Pad - FVector(0, 0, 50.f), FRotator::ZeroRotator);
		Slab->SetMobility(EComponentMobility::Movable);
		UStaticMeshComponent* C = Slab->GetStaticMeshComponent();
		C->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube")));
		C->SetWorldScale3D(FVector(2000.f, 2000.f, 1.f));
		C->SetCollisionProfileName(TEXT("BlockAll"));
		C->SetPhysMaterialOverride(LoadObject<UPhysicalMaterial>(nullptr, TEXT("/Game/Berat/Physics/PM_Asphalt.PM_Asphalt")));
		M->SetPhysicsLinearVelocity(FVector::ZeroVector);
		M->SetPhysicsAngularVelocityInDegrees(FVector::ZeroVector);
		Car->SetActorLocationAndRotation(Pad + FVector(-80000.f, 0, 60.f), FRotator::ZeroRotator, false, nullptr, ETeleportType::TeleportPhysics);
		Car->AutoThrottle = 0.f; Car->AutoBrake = 0.f; Car->AutoSteer = 0.f;
		UE_LOG(LogTemp, Display, TEXT("[berat-handling] %s: mass %.0f kg, CoM override %d %s, inertia scale %s, drag %.2f, downforce %.2f"),
			Name, W->Mass, W->bEnableCenterOfMassOverride, *W->CenterOfMassOverride.ToString(), *W->InertiaTensorScale.ToString(),
			W->DragCoefficient, W->DownforceCoefficient);
		for (int32 i = 0; i < W->Wheels.Num(); ++i)
		{
			const UChaosVehicleWheel* Wh = W->Wheels[i];
			UE_LOG(LogTemp, Display, TEXT("[berat-handling]   wheel %d %s: r %.0f, friction x%.2f, cornering %.0f, long stiff %.0f, slip thr %.0f, skid thr %.0f, spring %.0f preload %.0f damping %.2f travel +%.0f/-%.0f, brake %.0f, handbrake %.0f (%d), steer %.0f"),
				i, *GetNameSafe(Wh->GetClass()), Wh->WheelRadius, Wh->FrictionForceMultiplier, Wh->CorneringStiffness,
				Wh->SideSlipModifier * 0.f + Wh->SlipThreshold * 0.f, Wh->SlipThreshold, Wh->SkidThreshold, Wh->SpringRate, Wh->SpringPreload,
				Wh->SuspensionDampingRatio, Wh->SuspensionMaxRaise, Wh->SuspensionMaxDrop, Wh->MaxBrakeTorque, Wh->MaxHandBrakeTorque,
				Wh->bAffectedByHandbrake, Wh->MaxSteerAngle);
		}
		Next();
		break;
	}
	case 0:    // settle 3 s
		if (HClock > 3.f) { HStartPos = Car->GetActorLocation(); Next(); }
		break;
	case 1:    // launch: full throttle 15 s
		Car->AutoThrottle = 1.f;
		if (HSum[0] == 0.f && Kmh >= 50.f) HSum[0] = HClock;
		if (HSum[1] == 0.f && Kmh >= 100.f) HSum[1] = HClock;
		HMaxSlip = FMath::Max(HMaxSlip, FMath::Abs(Slip));
		if (HClock > 15.f)
		{
			UE_LOG(LogTemp, Display, TEXT("[berat-handling] %s launch: 0-50 %.2f s, 0-100 %.2f s, 15 s %.0f km/h gear %d rpm %.0f, max slip %.1f deg, heading drift %.1f m"),
				Name, HSum[0], HSum[1], Kmh, Car->GetGear(), Car->GetRpm(), HMaxSlip, FMath::Abs(Car->GetActorLocation().Y - HStartPos.Y) / 100.f);
			HStartPos = Car->GetActorLocation();
			const float From = Kmh;
			Next();
			HSum[2] = From;
		}
		break;
	case 2:    // brake to rest
		Car->AutoThrottle = 0.f; Car->AutoBrake = Kmh > 2.f ? 1.f : 0.f;
		HMaxSlip = FMath::Max(HMaxSlip, FMath::Abs(Slip));
		if (Kmh <= 2.f || HClock > 15.f)
		{
			const float D = FVector::Dist2D(Car->GetActorLocation(), HStartPos) / 100.f;
			UE_LOG(LogTemp, Display, TEXT("[berat-handling] %s braking from %.0f km/h: %.1f m in %.2f s (%.2f g mean), max slip %.1f deg"),
				Name, HSum[2], D, HClock, (HSum[2] / 3.6f) / FMath::Max(HClock, 0.01f) / 9.81f, HMaxSlip);
			Next();
		}
		break;
	case 3:    // steady circle: 60 km/h held, steering 0.5 after 3 s, measured 6..12 s
	{
		const float Want = 60.f;
		Car->AutoBrake = 0.f;
		Car->AutoThrottle = FMath::Clamp((Want - Kmh) / 10.f + 0.25f, 0.f, 1.f);
		Car->AutoSteer = HClock > 4.f ? 0.5f : 0.f;
		if (HClock > 8.f)
		{
			HSum[0] += LatG; HSum[1] += FMath::Abs(YawRate); HSum[2] += Slip; HSum[3] += Roll; HSum[4] += Kmh; ++HCount;
		}
		if (HClock > 14.f)
		{
			const float N = FMath::Max(HCount, 1);
			UE_LOG(LogTemp, Display, TEXT("[berat-handling] %s circle, steer 0.5: %.0f km/h, lateral %.2f g, yaw %.1f deg/s (radius %.1f m), body slip %.1f deg, roll %.1f deg"),
				Name, HSum[4] / N, HSum[0] / N, HSum[1] / N, (HSum[4] / N / 3.6f) / FMath::Max(FMath::DegreesToRadians(HSum[1] / N), 0.01f), HSum[2] / N, HSum[3] / N);
			Next();
		}
		break;
	}
	case 4:    // straighten, run up to 100 km/h
		Car->AutoSteer = 0.f; Car->AutoBrake = 0.f;
		Car->AutoThrottle = Kmh < 100.f ? 1.f : 0.3f;
		if (Kmh >= 100.f || HClock > 20.f) { Next(); }
		break;
	case 5:    // lane change: steer +0.35 for 0.6 s, -0.35 for 0.6 s, centre; watch 4 s
	{
		Car->AutoThrottle = 0.35f;
		Car->AutoSteer = HClock < 0.6f ? 0.35f : HClock < 1.2f ? -0.35f : 0.f;
		HMaxSlip = FMath::Max(HMaxSlip, FMath::Abs(Slip));
		HSum[0] = FMath::Max(HSum[0], FMath::Abs(YawRate));
		HSum[1] = FMath::Max(HSum[1], FMath::Abs(Roll));
		if (HClock > 5.f)
		{
			UE_LOG(LogTemp, Display, TEXT("[berat-handling] %s lane change at 100: max slip %.1f deg, max yaw %.0f deg/s, max roll %.1f deg, end slip %.1f deg yaw %.1f deg/s (%s), %.0f km/h"),
				Name, HMaxSlip, HSum[0], HSum[1], Slip, YawRate, FMath::Abs(YawRate) > 15.f || HMaxSlip > 25.f ? TEXT("UNSTABLE") : TEXT("settled"), Kmh);
			Next();
		}
		break;
	}
	case 6:    // straighten, settle at 70 km/h
		Car->AutoSteer = 0.f; Car->AutoBrake = 0.f;
		Car->AutoThrottle = FMath::Clamp((70.f - Kmh) / 10.f + 0.3f, 0.f, 1.f);
		if (HClock > 6.f) { HSum[5] = Car->GetActorRotation().Yaw; Next(); }
		break;
	case 7:    // handbrake turn: handbrake + steer 0.8 for 1.5 s, then release; heading change and slip
	{
		const bool bHold = HClock < 1.5f;
		Car->GetVehicleMovementComponent()->SetHandbrakeInput(bHold);
		Car->AutoThrottle = bHold ? 0.f : 0.4f;
		Car->AutoSteer = bHold ? 0.8f : 0.f;
		HMaxSlip = FMath::Max(HMaxSlip, FMath::Abs(Slip));
		if (HClock > 4.f)
		{
			const float Turned = FMath::Abs(FRotator::NormalizeAxis(Car->GetActorRotation().Yaw - HSum[5]));
			UE_LOG(LogTemp, Display, TEXT("[berat-handling] %s handbrake turn from 70: turned %.0f deg, max slip %.0f deg, end %.0f km/h, end yaw %.1f deg/s"),
				Name, Turned, HMaxSlip, Kmh, YawRate);
			Next();
		}
		break;
	}
	default:
		FGenericPlatformMisc::RequestExit(false);
		break;
	}
}


void ABeratHUD::LoadMap()
{
	if (bMapLoaded)
	{
		return;
	}
	bMapLoaded = true;
	MapTex = LoadObject<UTexture2D>(nullptr, TEXT("/Game/Berat/UI/T_Map.T_Map"));
	FString Json;
	if (FFileHelper::LoadFileToString(Json, *(FPaths::ProjectContentDir() / TEXT("Berat/UI/map.json"))))
	{
		TSharedPtr<FJsonObject> O;
		if (FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), O) && O)
		{
			MapWest = O->GetNumberField(TEXT("west"));
			MapNorth = O->GetNumberField(TEXT("north"));
			MapMpp = O->GetNumberField(TEXT("metres_per_pixel"));
			MapPixels = O->GetNumberField(TEXT("pixels"));
		}
	}
	if (MapMpp <= 0.0 || MapPixels <= 1.0)
	{
		MapTex = nullptr;
	}
}

void ABeratHUD::DrawMap(float X, float Y, float Size, double CentreU, double CentreV, double SpanUV, bool bFrame)
{
	// map texture window [centre - span/2, centre + span/2] in UV, drawn into a square; markers on top
	const double H = SpanUV * 0.5;
	FCanvasTileItem Tile(FVector2D(X, Y), MapTex->GetResource(), FVector2D(Size, Size),
		FVector2D(CentreU - H, CentreV - H), FVector2D(CentreU + H, CentreV + H), FLinearColor(1.f, 1.f, 1.f, 0.92f));
	Tile.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Tile);
	auto ToScreen = [&](const FVector& W) -> FVector2D
	{
		const double U = (W.X / 100.0 - MapWest) / MapMpp / MapPixels, V = (MapNorth + W.Y / 100.0) / MapMpp / MapPixels;
		return FVector2D(X + (U - (CentreU - H)) / SpanUV * Size, Y + (V - (CentreV - H)) / SpanUV * Size);
	};
	auto Inside = [&](const FVector2D& S) { return S.X > X + 3.f && S.Y > Y + 3.f && S.X < X + Size - 3.f && S.Y < Y + Size - 3.f; };
	// traffic
	TArray<FVector> Cars;
	for (TActorIterator<ABeratTraffic> It(GetWorld()); It; ++It)
	{
		It->GetCarPositions(Cars);
	}
	const float Dot = bMapOpen ? 3.f : 4.f;
	for (const FVector& C : Cars)
	{
		const FVector2D S = ToScreen(C);
		if (Inside(S))
		{
			DrawRect(FLinearColor(0.15f, 0.35f, 0.9f), S.X - Dot * 0.5f, S.Y - Dot * 0.5f, Dot, Dot);
		}
	}
	// the player: an arrow along the heading (Unreal yaw: 0 east, +90 south, as the map's screen axes)
	if (const APawn* Pawn = GetOwningPawn())
	{
		const FVector2D C = ToScreen(Pawn->GetActorLocation());
		const float Yaw = FMath::DegreesToRadians(Pawn->GetActorRotation().Yaw);
		const FVector2D F(FMath::Cos(Yaw), FMath::Sin(Yaw)), R(-F.Y, F.X);
		const float A = bMapOpen ? 9.f : 11.f;
		FCanvasTriangleItem Tri(C + F * A * 1.3f, C - F * A * 0.8f + R * A * 0.75f, C - F * A * 0.8f - R * A * 0.75f, GWhiteTexture);
		Tri.SetColor(FLinearColor(1.f, 0.45f, 0.05f));
		Canvas->DrawItem(Tri);
	}
	if (bFrame)
	{
		const FLinearColor Edge(0.f, 0.f, 0.f, 0.7f);
		DrawRect(Edge, X - 2.f, Y - 2.f, Size + 4.f, 2.f);
		DrawRect(Edge, X - 2.f, Y + Size, Size + 4.f, 2.f);
		DrawRect(Edge, X - 2.f, Y, 2.f, Size);
		DrawRect(Edge, X + Size, Y, 2.f, Size);
	}
}
