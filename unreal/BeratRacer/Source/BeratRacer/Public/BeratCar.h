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

// The arcade-sim layer on top of Chaos: the vehicle blueprint sets engine, gears, wheels and suspension; this adds what makes
// it drive like Forza Horizon rather than a raw rigid body: speed-sensitive steering, counter-steer and yaw stability, downforce,
// air control, auto-righting, a chase camera, lights that follow the time of day.
USTRUCT(BlueprintType)
struct FBeratDriveAssists
{
	GENERATED_BODY()

	// Steering lock kept at high speed (fraction of full lock at 45 m/s).
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float SteerAtSpeed = 0.32f;
	// Steering rates (input units per second), towards a turn and back to centre.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float SteerRate = 3.5f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float SteerReturnRate = 6.f;
	// Counter-steer added per radian of slip angle (0 off, 1 full).
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float Countersteer = 0.6f;
	// Yaw damping beyond the slip threshold (1/s): how hard a slide is caught.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float YawStability = 1.6f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float SlipThresholdDeg = 7.f;
	// Downforce in g at 50 m/s (grows with the square of speed).
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float DownforceG = 0.35f;
	// Angular damping in the air (1/s) and the pull back to level.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float AirDamping = 2.5f;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Assists") float AirLevelling = 1.2f;
};

UCLASS(Abstract)
class BERATRACER_API ABeratCar : public AWheeledVehiclePawn
{
	GENERATED_BODY()

public:
	ABeratCar();

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") FBeratDriveAssists Assists;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") FText DisplayName;
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

protected:
	virtual void BeginPlay() override;

private:
	void PlaceLights();
	void ApplyAssists(float Dt);
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

	float ThrottleIn = 0.f, BrakeIn = 0.f, SteerIn = 0.f, Steer = 0.f;
	FVector2D LookIn = FVector2D::ZeroVector;
	float LookYaw = 0.f, LookPitch = 0.f;
	int32 CameraMode = 0;            // 0 chase, 1 far chase, 2 bonnet
	int32 LightMode = 0;             // 0 auto, 1 on, 2 off
	float UpsideDownTime = 0.f;
};
