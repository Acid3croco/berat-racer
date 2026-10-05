#include "BeratCar.h"
#include "EngineUtils.h"
#include "BeratLaneGraph.h"
#include "BeratTraffic.h"

#include "BeratTimeOfDay.h"
#include "BeratEngineSound.h"
#include "NiagaraFunctionLibrary.h"
#include "Kismet/GameplayStatics.h"
#include "Components/DecalComponent.h"
#include "NiagaraSystem.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "Camera/CameraComponent.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "ChaosVehicleWheel.h"
#include "Components/PointLightComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/CollisionProfile.h"
#include "Components/SpotLightComponent.h"
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "Engine/LocalPlayer.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/SpringArmComponent.h"
#include "InputAction.h"
#include "InputMappingContext.h"
#include "InputModifiers.h"

static TAutoConsoleVariable<int32> CVarArcadeControls(TEXT("berat.ArcadeControls"), 0,
	TEXT("1: Chaos TorqueControl / TargetRotationControl presets on the player's car"));

namespace
{
	const float CameraArm[] = {620.f, 900.f, 0.f};
	const float CameraHeight[] = {60.f, 140.f, 0.f};
}

ABeratCar::ABeratCar()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.TickGroup = TG_PrePhysics;

	// What Epic's vehicle template base sets: a simulated rigid body with the Vehicle collision profile under Chaos.
	GetMesh()->SetCollisionProfileName(UCollisionProfile::Vehicle_ProfileName);
	GetMesh()->BodyInstance.bSimulatePhysics = true;
	GetMesh()->BodyInstance.bNotifyRigidBodyCollision = true;
	GetMesh()->BodyInstance.bUseCCD = true;
	GetMesh()->bBlendPhysics = true;
	GetMesh()->SetGenerateOverlapEvents(true);
	GetMesh()->SetCanEverAffectNavigation(false);

	Arm = CreateDefaultSubobject<USpringArmComponent>(TEXT("Arm"));
	Arm->SetupAttachment(GetMesh());
	// the mesh origin sits at road level: start the arm 1.2 m up, or its collision probe touches the road and collapses it
	Arm->SetRelativeLocation(FVector(0, 0, 120.f));
	Arm->TargetArmLength = CameraArm[0];
	Arm->SocketOffset = FVector(0, 0, CameraHeight[0]);
	Arm->bUsePawnControlRotation = false;
	Arm->bInheritPitch = false;
	Arm->bInheritRoll = false;
	Arm->bEnableCameraLag = true;
	Arm->CameraLagSpeed = 12.f;
	Arm->bEnableCameraRotationLag = true;
	Arm->CameraRotationLagSpeed = 7.f;
	Arm->bDoCollisionTest = true;
	Arm->ProbeSize = 15.f;

	Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
	Camera->SetupAttachment(Arm);
	Camera->SetRelativeRotation(FRotator(-6.f, 0, 0));
	Camera->FieldOfView = 75.f;

	auto MakeHead = [this](const TCHAR* Name)
	{
		USpotLightComponent* L = CreateDefaultSubobject<USpotLightComponent>(Name);
		L->SetupAttachment(GetMesh());
		L->SetIntensityUnits(ELightUnits::Candelas);
		L->SetInnerConeAngle(14.f);
		L->SetOuterConeAngle(34.f);
		L->SetLightColor(FLinearColor(1.f, 0.95f, 0.86f));
		L->SetCastShadows(true);
		L->SetVisibility(false);
		return L;
	};
	HeadL = MakeHead(TEXT("HeadL"));
	HeadR = MakeHead(TEXT("HeadR"));
	auto MakeTail = [this](const TCHAR* Name)
	{
		UPointLightComponent* L = CreateDefaultSubobject<UPointLightComponent>(Name);
		L->SetupAttachment(GetMesh());
		L->SetIntensityUnits(ELightUnits::Candelas);
		L->SetLightColor(FLinearColor(1.f, 0.05f, 0.02f));
		L->SetAttenuationRadius(120.f);
		L->SetCastShadows(false);
		L->SetVisibility(false);
		return L;
	};
	TailL = MakeTail(TEXT("TailL"));
	TailR = MakeTail(TEXT("TailR"));
	Sound = CreateDefaultSubobject<UBeratEngineSound>(TEXT("Sound"));
	Sound->SetupAttachment(GetMesh());
	Sound->bAutoActivate = true;
}

void ABeratCar::PostInitializeComponents()
{
	ApplyTemplate();
	Super::PostInitializeComponents();
}

void ABeratCar::NotifyHit(UPrimitiveComponent* MyComp, AActor* Other, UPrimitiveComponent* OtherComp, bool bSelfMoved,
	FVector HitLocation, FVector HitNormal, FVector NormalImpulse, const FHitResult& Hit)
{
	Super::NotifyHit(MyComp, Other, OtherComp, bSelfMoved, HitLocation, HitNormal, NormalImpulse, Hit);
	static double Last = 0.0;
	const double Now = GetWorld()->GetTimeSeconds();
	if (NormalImpulse.Size() > 2000.f && Now - Last > 0.25)
	{
		Last = Now;
		UE_LOG(LogTemp, Display, TEXT("[berat-hit] t %.2f s: %s / %s (my body %s) at (%.2f, %.2f, %.2f) m, normal (%.2f, %.2f, %.2f), impulse %.0f, %.1f km/h"),
			Now, *GetNameSafe(Other), *GetNameSafe(OtherComp), *Hit.MyBoneName.ToString(), HitLocation.X / 100.0, -HitLocation.Y / 100.0,
			HitLocation.Z / 100.0, HitNormal.X, -HitNormal.Y, HitNormal.Z, NormalImpulse.Size(), GetSpeedKmh());
	}
}

void ABeratCar::ApplyTemplate()
{
	UClass* C = Template.LoadSynchronous();
	if (!C)
	{
		return;
	}
	const AWheeledVehiclePawn* T = C->GetDefaultObject<AWheeledVehiclePawn>();
	// Body: skeletal mesh (its physics asset comes with it), animation (wheel spin and suspension), materials, collision.
	USkeletalMeshComponent* Src = T->GetMesh();
	USkeletalMeshComponent* Dst = GetMesh();
	Dst->SetSkeletalMeshAsset(Src->GetSkeletalMeshAsset());
	Dst->SetAnimInstanceClass(Src->GetAnimClass());
	for (int32 i = 0; i < Src->GetNumMaterials(); ++i)
	{
		Dst->SetMaterial(i, Src->GetMaterial(i));
	}
	Dst->SetCollisionProfileName(Src->GetCollisionProfileName());
	Dst->SetSimulatePhysics(true);
	// Chaos setup, copied field by field (the template's own pointers, to its mesh, stay behind).
	const UChaosWheeledVehicleMovementComponent* MS = Cast<UChaosWheeledVehicleMovementComponent>(T->GetVehicleMovementComponent());
	UChaosWheeledVehicleMovementComponent* MD = Cast<UChaosWheeledVehicleMovementComponent>(GetVehicleMovementComponent());
	if (MS && MD)
	{
		MD->WheelSetups = MS->WheelSetups;
		MD->EngineSetup = MS->EngineSetup;
		MD->TransmissionSetup = MS->TransmissionSetup;
		MD->DifferentialSetup = MS->DifferentialSetup;
		MD->SteeringSetup = MS->SteeringSetup;
		MD->Mass = MS->Mass;
		MD->ChassisWidth = MS->ChassisWidth;
		MD->ChassisHeight = MS->ChassisHeight;
		MD->DragCoefficient = MS->DragCoefficient;
		MD->DownforceCoefficient = MS->DownforceCoefficient;
		MD->bEnableCenterOfMassOverride = MS->bEnableCenterOfMassOverride;
		MD->CenterOfMassOverride = MS->CenterOfMassOverride;
		MD->InertiaTensorScale = MS->InertiaTensorScale;
		MD->SleepThreshold = MS->SleepThreshold;
		MD->SleepSlopeLimit = MS->SleepSlopeLimit;
		MD->bLegacyWheelFrictionPosition = MS->bLegacyWheelFrictionPosition;
		MD->WheelTraceCollisionResponses = MS->WheelTraceCollisionResponses;
		MD->bReverseAsBrake = MS->bReverseAsBrake;
	}
	UE_LOG(LogTemp, Display, TEXT("[berat] %s takes %s: mesh %s, anim %s, %d wheels, max torque %.0f"), *GetName(), *C->GetName(),
		*GetNameSafe(Src->GetSkeletalMeshAsset()), *GetNameSafe(Src->GetAnimClass()), MS ? MS->WheelSetups.Num() : -1,
		MS ? MS->EngineSetup.MaxTorque : 0.f);
}

void ABeratCar::BeginPlay()
{
	Super::BeginPlay();
	if (Sound)
	{
		Sound->Start();     // a synth component plays from Start() (auto-activation alone stays silent)
	}

	ConfigureChaos();
	PlaceLights();
	HeadL->SetIntensity(HeadlightCandela);
	HeadR->SetIntensity(HeadlightCandela);
	HeadL->SetAttenuationRadius(HeadlightReach);
	HeadR->SetAttenuationRadius(HeadlightReach);
}

void ABeratCar::PlaceLights()
{
	// The body's local bounds (the skeletal mesh's reference pose), so any vehicle blueprint gets lamps at its corners.
	// the asset's own bounds (the component's are the physics bodies' until the first update)
	const USkeletalMesh* SK = GetMesh()->GetSkeletalMeshAsset();
	const FBoxSphereBounds B = SK ? SK->GetBounds() : GetMesh()->CalcBounds(FTransform::Identity);
	const FVector C = B.Origin, E = B.BoxExtent;
	auto At = [&](const FVector& F, float Side) { return C + FVector(F.X * E.X, Side * F.Y * E.Y, F.Z * E.Z); };
	HeadL->SetRelativeLocationAndRotation(At(HeadlightAt, -1.f), FRotator(-3.f, 0, 0));
	HeadR->SetRelativeLocationAndRotation(At(HeadlightAt, 1.f), FRotator(-3.f, 0, 0));
	TailL->SetRelativeLocation(At(TaillightAt, -1.f));
	TailR->SetRelativeLocation(At(TaillightAt, 1.f));
}

void ABeratCar::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);

	// Actions and bindings built in code: keyboard and gamepad, no assets to keep in step.
	auto Make = [this](const TCHAR* Name, EInputActionValueType Type)
	{
		UInputAction* A = NewObject<UInputAction>(this, Name);
		A->ValueType = Type;
		return A;
	};
	ThrottleAction = Make(TEXT("IA_Throttle"), EInputActionValueType::Axis1D);
	BrakeAction = Make(TEXT("IA_Brake"), EInputActionValueType::Axis1D);
	SteerAction = Make(TEXT("IA_Steer"), EInputActionValueType::Axis1D);
	HandbrakeAction = Make(TEXT("IA_Handbrake"), EInputActionValueType::Boolean);
	LookAction = Make(TEXT("IA_Look"), EInputActionValueType::Axis2D);
	CameraAction = Make(TEXT("IA_Camera"), EInputActionValueType::Boolean);
	LightsAction = Make(TEXT("IA_Lights"), EInputActionValueType::Boolean);
	ResetAction = Make(TEXT("IA_Reset"), EInputActionValueType::Boolean);

	Mapping = NewObject<UInputMappingContext>(this, TEXT("DrivingMapping"));
	Mapping->MapKey(ThrottleAction, EKeys::Gamepad_RightTriggerAxis);
	Mapping->MapKey(ThrottleAction, EKeys::W);
	Mapping->MapKey(ThrottleAction, EKeys::Up);
	Mapping->MapKey(BrakeAction, EKeys::Gamepad_LeftTriggerAxis);
	Mapping->MapKey(BrakeAction, EKeys::S);
	Mapping->MapKey(BrakeAction, EKeys::Down);
	Mapping->MapKey(SteerAction, EKeys::Gamepad_LeftX);
	Mapping->MapKey(SteerAction, EKeys::D);
	Mapping->MapKey(SteerAction, EKeys::Right);
	FEnhancedActionKeyMapping& KA = Mapping->MapKey(SteerAction, EKeys::A);
	KA.Modifiers.Add(NewObject<UInputModifierNegate>(Mapping));
	FEnhancedActionKeyMapping& KL = Mapping->MapKey(SteerAction, EKeys::Left);
	KL.Modifiers.Add(NewObject<UInputModifierNegate>(Mapping));
	Mapping->MapKey(HandbrakeAction, EKeys::SpaceBar);
	Mapping->MapKey(HandbrakeAction, EKeys::Gamepad_FaceButton_Right);
	FEnhancedActionKeyMapping& LX = Mapping->MapKey(LookAction, EKeys::Gamepad_RightX);
	(void)LX;
	FEnhancedActionKeyMapping& LY = Mapping->MapKey(LookAction, EKeys::Gamepad_RightY);
	UInputModifierSwizzleAxis* Swz = NewObject<UInputModifierSwizzleAxis>(Mapping);
	Swz->Order = EInputAxisSwizzle::YXZ;
	LY.Modifiers.Add(Swz);
	Mapping->MapKey(CameraAction, EKeys::C);
	Mapping->MapKey(CameraAction, EKeys::Gamepad_RightThumbstick);
	Mapping->MapKey(LightsAction, EKeys::L);
	Mapping->MapKey(LightsAction, EKeys::Gamepad_DPad_Up);
	Mapping->MapKey(ResetAction, EKeys::R);
	Mapping->MapKey(ResetAction, EKeys::Gamepad_Special_Left);

	if (APlayerController* PC = Cast<APlayerController>(GetController()))
	{
		if (UEnhancedInputLocalPlayerSubsystem* Sub = ULocalPlayer::GetSubsystem<UEnhancedInputLocalPlayerSubsystem>(PC->GetLocalPlayer()))
		{
			// one car's mapping at a time: the previous car's (destroyed on a car change) still claimed the same keys
			Sub->ClearAllMappings();
			Sub->AddMappingContext(Mapping, 0);
		}
	}

	if (UEnhancedInputComponent* In = Cast<UEnhancedInputComponent>(PlayerInputComponent))
	{
		In->BindAction(ThrottleAction, ETriggerEvent::Triggered, this, &ABeratCar::OnThrottle);
		In->BindAction(ThrottleAction, ETriggerEvent::Completed, this, &ABeratCar::OnThrottle);
		In->BindAction(BrakeAction, ETriggerEvent::Triggered, this, &ABeratCar::OnBrake);
		In->BindAction(BrakeAction, ETriggerEvent::Completed, this, &ABeratCar::OnBrake);
		In->BindAction(SteerAction, ETriggerEvent::Triggered, this, &ABeratCar::OnSteer);
		In->BindAction(SteerAction, ETriggerEvent::Completed, this, &ABeratCar::OnSteer);
		In->BindAction(HandbrakeAction, ETriggerEvent::Triggered, this, &ABeratCar::OnHandbrake);
		In->BindAction(HandbrakeAction, ETriggerEvent::Completed, this, &ABeratCar::OnHandbrake);
		In->BindAction(LookAction, ETriggerEvent::Triggered, this, &ABeratCar::OnLook);
		In->BindAction(LookAction, ETriggerEvent::Completed, this, &ABeratCar::OnLook);
		In->BindAction(CameraAction, ETriggerEvent::Started, this, &ABeratCar::OnCamera);
		In->BindAction(LightsAction, ETriggerEvent::Started, this, &ABeratCar::OnLights);
		In->BindAction(ResetAction, ETriggerEvent::Started, this, &ABeratCar::OnReset);
	}
}

void ABeratCar::OnHandbrake(const FInputActionValue& V)
{
	bHandbrake = V.Get<bool>();
	GetVehicleMovementComponent()->SetHandbrakeInput(bHandbrake);
}

void ABeratCar::OnCamera(const FInputActionValue&)
{
	CameraMode = (CameraMode + 1) % 3;
	Arm->TargetArmLength = CameraArm[CameraMode];
	Arm->SocketOffset = FVector(0, 0, CameraHeight[CameraMode]);
	Arm->bEnableCameraLag = CameraMode != 2;
	Arm->bEnableCameraRotationLag = CameraMode != 2;
	if (CameraMode == 2)
	{
		const FBoxSphereBounds B = GetMesh()->CalcBounds(FTransform::Identity);
		Arm->SetRelativeLocation(B.Origin + FVector(B.BoxExtent.X * 0.15, 0, B.BoxExtent.Z * 0.55));
	}
	else
	{
		Arm->SetRelativeLocation(FVector(0, 0, 120.f));
	}
}

void ABeratCar::ToggleLights()
{
	LightMode = (LightMode + 1) % 3;
}

float ABeratCar::GetSpeedKmh() const
{
	return GetVehicleMovementComponent()->GetForwardSpeed() * 0.036f;
}

int32 ABeratCar::GetGear() const
{
	return GetVehicleMovementComponent()->GetCurrentGear();
}

float ABeratCar::GetRpm() const
{
	const UChaosWheeledVehicleMovementComponent* W = Cast<UChaosWheeledVehicleMovementComponent>(GetVehicleMovementComponent());
	return W ? W->GetEngineRotationSpeed() : 0.f;
}

void ABeratCar::ResetOnRoad()
{
	// Onto the nearest lane within 300 m, along it, 1 m up, at rest; upright in place when there is none.
	USkeletalMeshComponent* M = GetMesh();
	FRotator R(0.f, GetActorRotation().Yaw, 0.f);
	FVector P = GetActorLocation() + FVector(0, 0, 150.f);
	for (TActorIterator<ABeratTraffic> It(GetWorld()); It; ++It)
	{
		const UBeratLaneGraph* G = It->Graph;
		if (!G)
		{
			continue;
		}
		const FVector Here = GetActorLocation();
		double Best = FMath::Square(30000.0);
		for (const FBeratLane& L : G->Lanes)
		{
			if (L.Kind != EBeratLaneKind::Lane)
			{
				continue;
			}
			for (int32 i = 0; i + 1 < L.Points.Num(); ++i)
			{
				const double D2 = FVector::DistSquared(L.Points[i], Here);
				if (D2 < Best)
				{
					Best = D2;
					P = L.Points[i] + FVector(0, 0, 100.f);
					R = FRotator(0.f, (L.Points[i + 1] - L.Points[i]).Rotation().Yaw, 0.f);
				}
			}
		}
	}
	M->SetPhysicsLinearVelocity(FVector::ZeroVector);
	M->SetPhysicsAngularVelocityInDegrees(FVector::ZeroVector);
	SetActorLocationAndRotation(P, R, false, nullptr, ETeleportType::TeleportPhysics);
	UpsideDownTime = 0.f;
}

void ABeratCar::Tick(float Dt)
{
	Super::Tick(Dt);
	if (Dt <= 0.f)
	{
		return;
	}
	ShapeInput(Dt);
	UpdateCamera(Dt);
	UpdateLights();
	UpdateGearbox(Dt);
	UpdateWheelFx(Dt);
	if (Sound)
	{
		// only the player's car is heard (traffic is kinematic, silent)
		UChaosWheeledVehicleMovementComponent* W = Cast<UChaosWheeledVehicleMovementComponent>(GetVehicleMovementComponent());
		float Slip = 0.f;
		for (int32 i = 0; W && i < W->GetNumWheels(); ++i)
		{
			const FWheelStatus& S = W->GetWheelState(i);
			if (S.bInContact)
			{
				Slip = FMath::Max(Slip, FMath::Max(FMath::Abs(S.SkidMagnitude), FMath::Abs(S.SlipMagnitude)));
			}
		}
		const float Throttle = GetVehicleMovementComponent()->GetThrottleInput();
		Sound->SetState(GetRpm(), Throttle, FMath::Clamp((Slip - 500.f) / 800.f, 0.f, 1.f), FMath::Abs(GetSpeedKmh()));
	}
}

void ABeratCar::UpdateGearbox(float Dt)
{
	// Chaos's gearbox, shifted by a throttle-dependent schedule: SetTargetGear with Chaos's automatic off. Reverse and
	// first from rest stay Chaos's (bReverseAsBrake picks them from the inputs).
	UChaosWheeledVehicleMovementComponent* W = Cast<UChaosWheeledVehicleMovementComponent>(GetVehicleMovementComponent());
	if (!W || Assists.ShiftUp.X <= 0.f)
	{
		return;
	}
	if (W->GetUseAutoGears())
	{
		W->SetUseAutomaticGears(false);
	}
	ShiftHold -= Dt;
	const int32 Gear = W->GetTargetGear();
	const TArray<float>& R = W->TransmissionSetup.ForwardGearRatios;
	if (Gear < 1 || Gear > R.Num() || ShiftHold > 0.f || W->GetCurrentGear() != Gear)
	{
		return;
	}
	const float Max = W->EngineSetup.MaxRPM;
	const float Rpm = W->GetEngineRotationSpeed();
	const float T = FMath::Clamp(W->GetThrottleInput(), 0.f, 1.f);
	const float Up = Max * FMath::Lerp(Assists.ShiftUp.X, Assists.ShiftUp.Y, T);
	const float Down = Max * FMath::Lerp(Assists.ShiftDown.X, Assists.ShiftDown.Y, T);
	if (Gear < R.Num() && Rpm > Up && (Rpm * R[Gear] / R[Gear - 1] > Down * 1.05f || Rpm > Max * 0.97f))   // on the limiter: always up
	{
		W->SetTargetGear(Gear + 1, false);
		ShiftHold = 0.6f;
	}
	else if (Gear > 1 && Rpm < Down && Rpm * R[Gear - 2] / R[Gear - 1] < Up * 0.95f)
	{
		W->SetTargetGear(Gear - 1, false);
		ShiftHold = 0.4f;
	}
}

void ABeratCar::UpdateWheelFx(float Dt)
{
	if (WheelFx.Num() == 0)
	{
		for (const TCHAR* N : {TEXT("Dust"), TEXT("Gravel"), TEXT("Grass"), TEXT("Mud")})
		{
			WheelFx.Add(LoadObject<UNiagaraSystem>(nullptr, *FString::Printf(TEXT("/Game/Berat/FX/NS_Wheel%s.NS_Wheel%s"), N, N)));
		}
	}
	if (!TrackMaterial)
	{
		TrackMaterial = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Berat/FX/M_TyreTrack.M_TyreTrack"));
	}
	UChaosWheeledVehicleMovementComponent* W = Cast<UChaosWheeledVehicleMovementComponent>(GetVehicleMovementComponent());
	if (!W)
	{
		return;
	}
	const FVector V = GetVelocity();
	const float Kmh = V.Size() * 0.036f;
	const FVector Back = Kmh > 3.f ? -V.GetSafeNormal() : -GetActorForwardVector();
	for (int32 i = 0; i < FMath::Min(W->GetNumWheels(), 8); ++i)
	{
		const FWheelStatus& S = W->GetWheelState(i);
		if (!S.bInContact || !S.PhysMaterial.IsValid())
		{
			WheelFxClock[i] = 0.f;
			bTrackHave[i] = false;
			continue;
		}
		// tracks: rear wheels (Chaos order FL, FR, RL, RR) on soft ground, a strip from the last mark every 1.5 m
		const EPhysicalSurface Soft = S.PhysMaterial->SurfaceType;
		if (i >= 2 && TrackMaterial && Soft >= SurfaceType3 && Soft <= SurfaceType8)
		{
			const FVector P = S.ContactPoint;
			if (!bTrackHave[i])
			{
				TrackLast[i] = P;
				bTrackHave[i] = true;
			}
			const FVector D = P - TrackLast[i];
			const float L = D.Size2D();
			if (L > 300.f)
			{
				TrackLast[i] = P;                                   // a jump (teleport, air): restart
			}
			else if (L >= 150.f)
			{
				const FRotator R = FRotationMatrix::MakeFromXY(FVector(0, 0, -1), D.GetSafeNormal2D()).Rotator();
				if (UDecalComponent* Dc = UGameplayStatics::SpawnDecalAtLocation(this, TrackMaterial,
					FVector(40.f, L * 0.5f + 8.f, 12.f), (P + TrackLast[i]) * 0.5f, R, 60.f))
				{
					Dc->SetFadeOut(45.f, 15.f, false);
					++WheelTracksSpawned;
					Dc->SetFadeScreenSize(0.002f);
				}
				TrackLast[i] = P;
			}
		}
		else
		{
			bTrackHave[i] = false;
		}
		// slip: wheel spin or slide (cm/s); intensity grows with speed and slip
		const float Slip = FMath::Max(FMath::Abs(S.SlipMagnitude), FMath::Abs(S.SkidMagnitude));
		// wheels spinning on the spot dig, they do not raise a cloud: slip counts less below 15 km/h
		float Intensity = FMath::Clamp(Kmh / 60.f, 0.f, 1.f)
			+ FMath::Clamp(Slip / 500.f, 0.f, 1.f) * FMath::GetMappedRangeValueClamped(FVector2f(3.f, 15.f), FVector2f(0.25f, 1.f), Kmh);
		// surface types: DefaultEngine.ini PhysicsSettings (berat_import.SURFACES)
		int32 Fx = INDEX_NONE;
		switch (S.PhysMaterial->SurfaceType)
		{
		case SurfaceType1: case SurfaceType2:                  // asphalt, concrete: tyre smoke when sliding hard
			Intensity = FMath::Clamp((Slip - 900.f) / 600.f, 0.f, 1.5f);
			Fx = 1;
			break;
		case SurfaceType3: Fx = 1; break;                       // gravel
		case SurfaceType4: case SurfaceType6: Fx = 0; break;    // dirt, field
		case SurfaceType8: Fx = 0; Intensity *= 0.5f; break;    // forest floor
		case SurfaceType5: Fx = 2; break;                       // grass
		case SurfaceType7: Fx = 3; break;                       // mud
		default: break;
		}
		if (Fx == INDEX_NONE || !WheelFx[Fx] || Intensity < 0.15f)
		{
			continue;
		}
		WheelFxClock[i] += Dt;
		if (WheelFxClock[i] < FMath::Clamp(0.12f / Intensity, 0.07f, 0.4f))
		{
			continue;
		}
		WheelFxClock[i] = 0.f;
		const FVector Up = (FVector::UpVector + Back * 0.7f).GetSafeNormal();
		UNiagaraFunctionLibrary::SpawnSystemAtLocation(this, WheelFx[Fx], S.ContactPoint + FVector(0, 0, 10.f),
			FRotationMatrix::MakeFromZ(Up).Rotator(), FVector::OneVector, true, true, ENCPoolMethod::AutoRelease, true);
		++WheelFxSpawned;
	}
}

void ABeratCar::ConfigureChaos()
{
	UChaosWheeledVehicleMovementComponent* W = Cast<UChaosWheeledVehicleMovementComponent>(GetVehicleMovementComponent());
	if (!W || !Assists.bApplyToVehicle)
	{
		return;
	}
	// Aerodynamics: Chaos applies drag and downforce from these with the speed squared.
	// set through Chaos's runtime setters: the vehicle is already simulating (rebuilding its physics state threw it into the sky)
	W->SetDownforceCoefficient(Assists.DownforceCoefficient);
	W->SetDragCoefficient(Assists.DragCoefficient);

	// Steering lock against speed (km/h): full lock parked, SteerAtSpeed at 160 km/h.
	FRichCurve* Curve = W->SteeringSetup.SteeringCurve.GetRichCurve();
	Curve->Reset();
	Curve->AddKey(0.f, 1.f);
	Curve->AddKey(40.f, FMath::Lerp(1.f, Assists.SteerAtSpeed, 0.35f));
	Curve->AddKey(100.f, FMath::Lerp(1.f, Assists.SteerAtSpeed, 0.8f));
	Curve->AddKey(160.f, Assists.SteerAtSpeed);
	W->SteeringInputRate.RiseRate = Assists.SteerRise;
	W->SteeringInputRate.FallRate = Assists.SteerFall;

	// Brakes: torque per wheel for Assists.BrakeG at full brake, split 65 / 35 front / rear (T = m a r per axle share)
	if (Assists.BrakeG > 0.f && W->Wheels.Num() >= 4)
	{
		const float Force = W->Mass * 9.81f * Assists.BrakeG;                  // N
		const int32 N = W->Wheels.Num(), Front = N / 2;
		for (int32 i = 0; i < N; ++i)
		{
			const UChaosVehicleWheel* Wh = W->Wheels[i];
			const float Share = (i < Front ? 0.65f : 0.35f) / Front;
			const float Torque = Force * Share * (Wh ? Wh->WheelRadius : 33.f) / 100.f;
			W->SetWheelMaxBrakeTorque(i, Torque);
		}
		// handbrake: light, on the rear only (the grip loss does the turning)
		const float HbForce = W->Mass * 9.81f * Assists.HandbrakeG / FMath::Max(N - Front, 1);
		for (int32 i = 0; i < N; ++i)
		{
			const UChaosVehicleWheel* Wh = W->Wheels[i];
			BaseFriction[FMath::Min(i, 7)] = Wh ? Wh->FrictionForceMultiplier : 3.f;
			if (Wh && Wh->bAffectedByHandbrake)
			{
				W->SetWheelHandbrakeTorque(i, HbForce * Wh->WheelRadius / 100.f);
			}
		}
	}

	// Arcade controls of Chaos: level in the air, no roll-overs from a kerb, a little turn-in from steering.
	// Behind berat.ArcadeControls until tuned (their first settings threw the car into the sky).
	if (CVarArcadeControls.GetValueOnGameThread() == 0)
	{
		return;
	}
	W->TargetRotationControl.Enabled = Assists.AirLevelling > 0.f;
	W->TargetRotationControl.bRollVsSpeedEnabled = false;
	W->TargetRotationControl.RollControlScaling = Assists.AirLevelling;
	W->TargetRotationControl.PitchControlScaling = Assists.AirLevelling;
	W->TargetRotationControl.RollMaxAngle = 30.f;
	W->TargetRotationControl.PitchMaxAngle = 45.f;
	W->TargetRotationControl.RotationStiffness = 5.f;
	W->TargetRotationControl.RotationDamping = 0.6f;
	W->TargetRotationControl.MaxAccel = 4.f;
	W->TargetRotationControl.AutoCentreRollStrength = Assists.AirLevelling;
	W->TargetRotationControl.AutoCentrePitchStrength = Assists.AirLevelling * 0.5f;
	W->TargetRotationControl.AutoCentreYawStrength = 0.f;
	W->TorqueControl.Enabled = Assists.YawFromSteering > 0.f;
	W->TorqueControl.YawFromSteering = Assists.YawFromSteering;
	W->TorqueControl.YawTorqueScaling = 1.f;
	W->TorqueControl.RotationDamping = Assists.RotationDamping;
}

void ABeratCar::ShapeInput(float Dt)
{
	// Input only: the counter-steer hint a gamepad assist gives, from the slip angle; Chaos does the rest.
	UChaosVehicleMovementComponent* Move = GetVehicleMovementComponent();
	USkeletalMeshComponent* M = GetMesh();
	float Slip = 0.f;
	if (M->IsSimulatingPhysics())
	{
		const FTransform& T = M->GetComponentTransform();
		const FVector Fwd = T.GetUnitAxis(EAxis::X), Right = T.GetUnitAxis(EAxis::Y), Up = T.GetUnitAxis(EAxis::Z);
		const FVector Flat = FVector::VectorPlaneProject(M->GetPhysicsLinearVelocity(), Up);
		if (Flat.Size() > 300.f && FVector::DotProduct(Flat, Fwd) > 0.f)
		{
			Slip = FMath::Atan2(FVector::DotProduct(Flat, Right), FVector::DotProduct(Flat, Fwd));
		}
		// Stuck on the roof or the side: right it after 2 s.
		UpsideDownTime = (Up.Z < 0.3f && Flat.Size() < 300.f) ? UpsideDownTime + Dt : 0.f;
		if (UpsideDownTime > 2.f)
		{
			ResetOnRoad();
		}
	}
	const float SteerSrc = AutoThrottle >= 0.f ? AutoSteer : SteerIn;
	const float Steer = FMath::Clamp(SteerSrc + Assists.Countersteer * Slip / FMath::DegreesToRadians(35.f), -1.f, 1.f);
	Move->SetSteeringInput(Steer);
	Move->SetThrottleInput(AutoThrottle >= 0.f ? AutoThrottle : ThrottleIn);
	// experiment: keep the body awake while there is input (a sleeping Chaos body ignores the drive torque)
	if ((AutoThrottle >= 0.f ? AutoThrottle : ThrottleIn) > 0.01f && !M->IsAnyRigidBodyAwake())
	{
		UE_LOG(LogTemp, Display, TEXT("[berat] car body asleep under throttle: waking it"));
		M->WakeAllRigidBodies();
	}
	Move->SetBrakeInput(AutoThrottle >= 0.f ? AutoBrake : BrakeIn);
	// handbrake: the rear tyres lose grip while it is held (Chaos's per-wheel friction multiplier)
	const bool bHb = bHandbrake || Move->GetHandbrakeInput();
	UChaosWheeledVehicleMovementComponent* WM = Cast<UChaosWheeledVehicleMovementComponent>(Move);
	if (WM && bHb != bHandbrakeApplied && Assists.HandbrakeGrip > 0.f)
	{
		bHandbrakeApplied = bHb;
		for (int32 i = 0; i < FMath::Min(WM->Wheels.Num(), 8); ++i)
		{
			if (WM->Wheels[i] && WM->Wheels[i]->bAffectedByHandbrake && BaseFriction[i] > 0.f)
			{
				WM->SetWheelFrictionMultiplier(i, BaseFriction[i] * (bHb ? Assists.HandbrakeGrip : 1.f));
			}
		}
	}
}

void ABeratCar::UpdateCamera(float Dt)
{
	const float Speed = FMath::Abs(GetVehicleMovementComponent()->GetForwardSpeed()) / 100.f;
	// Field of view opens with speed; the free look springs back.
	const float Fov = FMath::Lerp(72.f, 90.f, FMath::Clamp((Speed - 10.f) / 50.f, 0.f, 1.f));
	Camera->SetFieldOfView(FMath::FInterpTo(Camera->FieldOfView, CameraMode == 2 ? Fov + 5.f : Fov, Dt, 3.f));
	if (LookIn.SizeSquared() > 0.04f)
	{
		LookYaw = LookIn.X * 170.f;
		LookPitch = LookIn.Y * 25.f;
	}
	else
	{
		LookYaw = FMath::FInterpTo(LookYaw, 0.f, Dt, 5.f);
		LookPitch = FMath::FInterpTo(LookPitch, 0.f, Dt, 5.f);
	}
	Arm->SetRelativeRotation(FRotator(-LookPitch - (CameraMode == 2 ? 0.f : 4.f), LookYaw, 0.f));
}

void ABeratCar::UpdateLights()
{
	const float Night = ABeratTimeOfDay::GetNightFactor(this);
	const bool bOn = LightMode == 1 || (LightMode == 0 && Night > 0.35f);
	if (HeadL->IsVisible() != bOn)
	{
		HeadL->SetVisibility(bOn);
		HeadR->SetVisibility(bOn);
	}
	// Tail lamps: on with the headlights, brighter (brake lights) when braking forward.
	const bool bBraking = BrakeIn > 0.1f && GetVehicleMovementComponent()->GetForwardSpeed() > 50.f;
	const bool bTail = bOn || bBraking;
	if (TailL->IsVisible() != bTail)
	{
		TailL->SetVisibility(bTail);
		TailR->SetVisibility(bTail);
	}
	const float Tail = bBraking ? 4.f : 0.5f;
	TailL->SetIntensity(Tail);
	TailR->SetIntensity(Tail);
}
