#include "BeratCar.h"

#include "BeratTimeOfDay.h"
#include "Camera/CameraComponent.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/SpotLightComponent.h"
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "Engine/LocalPlayer.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/SpringArmComponent.h"
#include "InputAction.h"
#include "InputMappingContext.h"
#include "InputModifiers.h"

namespace
{
	const float CameraArm[] = {620.f, 900.f, 0.f};
	const float CameraHeight[] = {170.f, 260.f, 0.f};
}

ABeratCar::ABeratCar()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.TickGroup = TG_PrePhysics;

	Arm = CreateDefaultSubobject<USpringArmComponent>(TEXT("Arm"));
	Arm->SetupAttachment(GetMesh());
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
		L->SetAttenuationRadius(350.f);
		L->SetCastShadows(false);
		L->SetVisibility(false);
		return L;
	};
	TailL = MakeTail(TEXT("TailL"));
	TailR = MakeTail(TEXT("TailR"));
}

void ABeratCar::BeginPlay()
{
	Super::BeginPlay();
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
	const FBoxSphereBounds B = GetMesh()->CalcBounds(FTransform::Identity);
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
	ThrottleAction = Make(TEXT("Throttle"), EInputActionValueType::Axis1D);
	BrakeAction = Make(TEXT("Brake"), EInputActionValueType::Axis1D);
	SteerAction = Make(TEXT("Steer"), EInputActionValueType::Axis1D);
	HandbrakeAction = Make(TEXT("Handbrake"), EInputActionValueType::Boolean);
	LookAction = Make(TEXT("Look"), EInputActionValueType::Axis2D);
	CameraAction = Make(TEXT("Camera"), EInputActionValueType::Boolean);
	LightsAction = Make(TEXT("Lights"), EInputActionValueType::Boolean);
	ResetAction = Make(TEXT("Reset"), EInputActionValueType::Boolean);

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
	GetVehicleMovementComponent()->SetHandbrakeInput(V.Get<bool>());
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
		Arm->SetRelativeLocation(FVector::ZeroVector);
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
	// Upright, 1.5 m up, same heading, at rest.
	USkeletalMeshComponent* M = GetMesh();
	const FRotator R(0.f, GetActorRotation().Yaw, 0.f);
	const FVector P = GetActorLocation() + FVector(0, 0, 150.f);
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
}

void ABeratCar::ConfigureChaos()
{
	UChaosWheeledVehicleMovementComponent* W = Cast<UChaosWheeledVehicleMovementComponent>(GetVehicleMovementComponent());
	if (!W || !Assists.bApplyToVehicle)
	{
		return;
	}
	// Aerodynamics: Chaos applies drag and downforce from these with the speed squared.
	W->DownforceCoefficient = Assists.DownforceCoefficient;
	W->DragCoefficient = Assists.DragCoefficient;

	// Steering lock against speed (km/h): full lock parked, SteerAtSpeed at 160 km/h.
	FRichCurve* Curve = W->SteeringSetup.SteeringCurve.GetRichCurve();
	Curve->Reset();
	Curve->AddKey(0.f, 1.f);
	Curve->AddKey(40.f, FMath::Lerp(1.f, Assists.SteerAtSpeed, 0.35f));
	Curve->AddKey(100.f, FMath::Lerp(1.f, Assists.SteerAtSpeed, 0.8f));
	Curve->AddKey(160.f, Assists.SteerAtSpeed);
	W->SteeringInputRate.RiseRate = Assists.SteerRise;
	W->SteeringInputRate.FallRate = Assists.SteerFall;

	// Arcade controls of Chaos: level in the air, no roll-overs from a kerb, a little turn-in from steering.
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
	W->RecreatePhysicsState();
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
	const float Steer = FMath::Clamp(SteerIn + Assists.Countersteer * Slip / FMath::DegreesToRadians(35.f), -1.f, 1.f);
	Move->SetSteeringInput(Steer);
	Move->SetThrottleInput(ThrottleIn);
	Move->SetBrakeInput(BrakeIn);
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
	const float Tail = bBraking ? 60.f : 12.f;
	TailL->SetIntensity(Tail);
	TailR->SetIntensity(Tail);
}
