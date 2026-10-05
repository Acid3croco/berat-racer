#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimInstanceProxy.h"
#include "ChaosVehicleWheel.h"
#include "BeratWheels.generated.h"

// Road-car wheel defaults for cars built from a skeletal mesh alone (the Vehicle Variety Pack's UE4 PhysX blueprints do not
// load in UE5): each car's blueprint subclass sets its radius and width.
UCLASS(Blueprintable)
class BERATRACER_API UBeratWheelFront : public UChaosVehicleWheel
{
	GENERATED_BODY()
public:
	UBeratWheelFront();
};

UCLASS(Blueprintable)
class BERATRACER_API UBeratWheelRear : public UChaosVehicleWheel
{
	GENERATED_BODY()
public:
	UBeratWheelRear();
};

struct FBeratWheelPose
{
	FName Bone;
	float SpinDeg = 0.f;
	float SteerDeg = 0.f;
	float LiftCm = 0.f;
};

// Spins, steers and lifts the wheel bones from the Chaos wheels, on any car skeleton: no animation blueprint needed.
struct FBeratWheelProxy : public FAnimInstanceProxy
{
	FBeratWheelProxy() = default;
	explicit FBeratWheelProxy(UAnimInstance* In) : FAnimInstanceProxy(In) {}
	virtual void PreUpdate(UAnimInstance* InAnimInstance, float DeltaSeconds) override;
	virtual bool Evaluate(FPoseContext& Output) override;
	TArray<FBeratWheelPose> Wheels;
};

UCLASS(Transient, NotBlueprintable)
class BERATRACER_API UBeratWheelAnimInstance : public UAnimInstance
{
	GENERATED_BODY()
public:
	TArray<FBeratWheelPose> Wheels;
protected:
	virtual void NativeUpdateAnimation(float DeltaSeconds) override;
	virtual FAnimInstanceProxy* CreateAnimInstanceProxy() override { return new FBeratWheelProxy(this); }
	virtual void DestroyAnimInstanceProxy(FAnimInstanceProxy* InProxy) override { delete InProxy; }
};
