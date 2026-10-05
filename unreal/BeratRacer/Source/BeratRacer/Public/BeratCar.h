#pragma once

#include "CoreMinimal.h"
#include "WheeledVehiclePawn.h"
#include "InputActionValue.h"
#include "BeratCar.generated.h"

class USpringArmComponent;
class UCameraComponent;
class USpotLightComponent;
class UPointLightComponent;
class UInputAction;
class UInputMappingContext;

// Driving is Chaos Vehicles throughout: engine, gearbox, tyres and suspension come from the vehicle blueprint, and the
// arcade-sim feel comes from Chaos's own settings (aerodynamics, steering curve, input rates, the arcade torque / target
// rotation controls), applied from these presets at BeginPlay. The only thing added on top is input shaping: a
// counter-steer hint mixed into the steering input, as a gamepad assist would.
USTRUCT(BlueprintType)
struct FBeratDriveAssists
{
	GENERATED_BODY()

	// Steering kept at high speed (fraction of full lock at 160 km/h): Chaos SteeringSetup.SteeringCurve.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float SteerAtSpeed = 0.35f;
	// Chaos SteeringInputRate (input units per second): towards a turn, back to centre.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float SteerRise = 3.5f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float SteerFall = 6.f;
	// Counter-steer mixed into the input per radian of slip angle (0 off).
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float Countersteer = 0.5f;
	// Chaos aerodynamics.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float DownforceCoefficient = 0.6f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float DragCoefficient = 0.32f;
	// Chaos TargetRotationControl: keeps the car level in the air and stops it rolling over (0 off).
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float AirLevelling = 0.6f;
	// Chaos TorqueControl: yaw rate added from steering (an arcade turn-in), and its damping.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float YawFromSteering = 0.15f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float RotationDamping = 0.3f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") bool bApplyToVehicle = true;
};

UCLASS(Abstract)
class BERATRACER_API ABeratCar : public AWheeledVehiclePawn
{
	GENERATED_BODY()

public:
	ABeratCar();

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") FBeratDriveAssists Assists;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") FText DisplayName;
	// A Chaos vehicle blueprint (an engine template car, a Fab car...) whose body, animation, materials and whole Chaos setup
	// (wheels, engine, gearbox, differential, steering, mass) this car takes when it starts.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") TSoftClassPtr<AWheeledVehiclePawn> Template;
	// Headlight intensity (candela, per lamp) and reach.
	UPROPERTY(EditAnywhere, Category = "Berat|Lights") float HeadlightCandela = 30000.f;
	UPROPERTY(EditAnywhere, Category = "Berat|Lights") float HeadlightReach = 9000.f;
	// Lamp placement as fractions of the body's bounds (x forward, y right, z up), 0 the centre, 1 the edge.
	UPROPERTY(EditAnywhere, Category = "Berat|Lights") FVector HeadlightAt = FVector(0.97, 0.66, -0.1);
	UPROPERTY(EditAnywhere, Category = "Berat|Lights") FVector TaillightAt = FVector(-0.98, 0.7, 0.05);

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Berat") TObjectPtr<USpringArmComponent> Arm;
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Berat") TObjectPtr<UCameraComponent> Camera;
	UPROPERTY(VisibleAnywhere, Category = "Berat|Lights") TObjectPtr<USpotLightComponent> HeadL;
	UPROPERTY(VisibleAnywhere, Category = "Berat|Lights") TObjectPtr<USpotLightComponent> HeadR;
	UPROPERTY(VisibleAnywhere, Category = "Berat|Lights") TObjectPtr<UPointLightComponent> TailL;
	UPROPERTY(VisibleAnywhere, Category = "Berat|Lights") TObjectPtr<UPointLightComponent> TailR;

	UFUNCTION(BlueprintCallable, Category = "Berat") float GetSpeedKmh() const;
	UFUNCTION(BlueprintCallable, Category = "Berat") int32 GetGear() const;
	UFUNCTION(BlueprintCallable, Category = "Berat") float GetRpm() const;
	UFUNCTION(BlueprintCallable, Category = "Berat") void ResetOnRoad();

	// Lights: auto (follow the night), or forced by the player.
	UFUNCTION(BlueprintCallable, Category = "Berat") void ToggleLights();

	virtual void Tick(float DeltaSeconds) override;
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

	virtual void PostInitializeComponents() override;
	virtual void NotifyHit(UPrimitiveComponent* MyComp, AActor* Other, UPrimitiveComponent* OtherComp, bool bSelfMoved,
		FVector HitLocation, FVector HitNormal, FVector NormalImpulse, const FHitResult& Hit) override;

	// Test hook (ABeratPlayerController -BeratTest): when >= 0 these replace the player's throttle and steering.
	float AutoThrottle = -1.f;
	float AutoSteer = 0.f;
	float AutoBrake = 0.f;

protected:
	virtual void BeginPlay() override;

private:
	void PlaceLights();
	void ApplyTemplate();
	void ConfigureChaos();
	void ShapeInput(float Dt);
	void UpdateCamera(float Dt);
	void UpdateLights();

	void OnThrottle(const FInputActionValue& V) { ThrottleIn = V.Get<float>(); }
	void OnBrake(const FInputActionValue& V) { BrakeIn = V.Get<float>(); }
	void OnSteer(const FInputActionValue& V) { SteerIn = V.Get<float>(); }
	void OnHandbrake(const FInputActionValue& V);
	void OnLook(const FInputActionValue& V) { LookIn = V.Get<FVector2D>(); }
	void OnCamera(const FInputActionValue&);
	void OnLights(const FInputActionValue&) { ToggleLights(); }
	void OnReset(const FInputActionValue&) { ResetOnRoad(); }

	UPROPERTY(Transient) TObjectPtr<UInputMappingContext> Mapping;
	UPROPERTY(Transient) TObjectPtr<UInputAction> ThrottleAction;
	UPROPERTY(Transient) TObjectPtr<UInputAction> BrakeAction;
	UPROPERTY(Transient) TObjectPtr<UInputAction> SteerAction;
	UPROPERTY(Transient) TObjectPtr<UInputAction> HandbrakeAction;
	UPROPERTY(Transient) TObjectPtr<UInputAction> LookAction;
	UPROPERTY(Transient) TObjectPtr<UInputAction> CameraAction;
	UPROPERTY(Transient) TObjectPtr<UInputAction> LightsAction;
	UPROPERTY(Transient) TObjectPtr<UInputAction> ResetAction;

	float ThrottleIn = 0.f, BrakeIn = 0.f, SteerIn = 0.f;
	FVector2D LookIn = FVector2D::ZeroVector;
	float LookYaw = 0.f, LookPitch = 0.f;
	int32 CameraMode = 0;            // 0 chase, 1 far chase, 2 bonnet
	int32 LightMode = 0;             // 0 auto, 1 on, 2 off
	float UpsideDownTime = 0.f;
};
