#include "BeratGameMode.h"

#include "BeratCar.h"
#include "BeratTimeOfDay.h"
#include "BeratLaneGraph.h"
#include "BeratTraffic.h"
#include "ChaosVehicleMovementComponent.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerStart.h"
#include "HighResScreenshot.h"
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
	UE_LOG(LogTemp, Display, TEXT("[berat-test] %s: %d frames, avg %.2f ms (%.1f fps), p99 %.2f ms (%.1f fps), worst %.2f ms; speed %.1f km/h, gear %d, at x %.1f y %.1f z %.2f m"),
		Phase, S.Num(), Avg, 1000.f / Avg, P99, 1000.f / P99, S.Last(), Car ? Car->GetSpeedKmh() : 0.f, Car ? Car->GetGear() : 0,
		P.X / 100.0, -P.Y / 100.0, P.Z / 100.0);
	FrameMs.Reset();
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
			int32 Pick = Next[0];
			float Straight = -1.f;
			for (int32 N : Next)                     // the straightest continuation, no U-turns
			{
				const FBeratLane& NL = G->Lanes[N];
				const float Score = NL.Kind == EBeratLaneKind::UTurn ? -2.f : -FMath::Abs(NL.Turn);
				if (Straight < -0.5f || Score > Straight) { Straight = Score; Pick = N; }
			}
			PilotS -= G->Lanes[PilotLane].Length();
			PilotLane = Pick;
		}
	}
	const float Speed = FMath::Abs(Car->GetSpeedKmh());
	const float Look = 600.f + Speed * 25.f;                    // cm ahead: 6 m + 0.9 s at speed
	FVector Target, TDir;
	G->Sample(PilotLane, PilotS + Look, Target, TDir);
	const FVector Local = Car->GetActorTransform().InverseTransformPosition(Target);
	const float Steer = FMath::Clamp(FMath::Atan2(Local.Y, Local.X) * 2.2f, -1.f, 1.f);
	// target speed: the lane's own (curvature and limit), capped
	const FBeratLane& L = G->Lanes[PilotLane];
	const int32 Pt = FMath::Clamp(Algo::LowerBound(L.Distance, PilotS + Look), 0, L.Speed.Num() - 1);
	const float Want = FMath::Min(PilotKmh, L.Speed.IsValidIndex(Pt) ? L.Speed[Pt] * 3.6f : PilotKmh);
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
	Car->AutoBrake = Speed > Want + 8.f ? FMath::Clamp((Speed - Want) / 20.f, 0.f, 1.f) : 0.f;
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
	ABeratCar* Car = Cast<ABeratCar>(GetPawn());
	// -BeratStart=x,y (package metres): put the car on the nearest lane there, along it, once
	FString StartArg;
	if (Car && TestStep == 0 && TestClock > 1.f && !bMoved && FParse::Value(FCommandLine::Get(), TEXT("BeratStart="), StartArg))
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
	if (TestStep >= 2 && Car)
	{
		Pilot(Car, Dt);
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
		case 1: TestReport(TEXT("parked")); break;
		case 2: TestShot(TEXT("game_drive")); break;
		case 3: TestReport(TEXT("driving 1")); break;
		case 4: TestReport(TEXT("driving 2")); TestShot(TEXT("game_drive2")); break;
		case 5: FGenericPlatformMisc::RequestExit(false); break;
		}
		++TestStep;
	}
}

void ABeratPlayerController::SetupInputComponent()
{
	Super::SetupInputComponent();
	// Plain key bindings for the few menu-like actions; driving input lives on the car (Enhanced Input).
	InputComponent->BindKey(EKeys::Tab, IE_Pressed, this, &ABeratPlayerController::NextCar);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Right, IE_Pressed, this, &ABeratPlayerController::NextCar);
	InputComponent->BindKey(EKeys::T, IE_Pressed, this, &ABeratPlayerController::TimeForward);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Left, IE_Pressed, this, &ABeratPlayerController::TimeForward);
	InputComponent->BindKey(EKeys::Y, IE_Pressed, this, &ABeratPlayerController::TimeBack);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Down, IE_Pressed, this, &ABeratPlayerController::TimeBack);
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
	DrawText(TEXT("Tab / D-pad right: next car   T / Y: time +-1 h   L: lights   C: camera   R: reset"),
		FLinearColor(1.f, 1.f, 1.f, 0.6f), 20.f, Canvas->ClipY - 30.f, Small, 1.f);
}
