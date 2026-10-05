#include "BeratWheels.h"

#include "Animation/AnimNodeBase.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "WheeledVehiclePawn.h"

UBeratWheelFront::UBeratWheelFront()
{
	WheelRadius = 33.f;
	WheelWidth = 22.f;
	bAffectedBySteering = true;
	MaxSteerAngle = 38.f;
	bAffectedByHandbrake = false;
	bAffectedByEngine = true;
	FrictionForceMultiplier = 3.f;
	CorneringStiffness = 1000.f;
	SuspensionMaxRaise = 10.f;
	SuspensionMaxDrop = 10.f;
	SpringRate = 250.f;
	SpringPreload = 50.f;
	SuspensionDampingRatio = 0.5f;
	MaxBrakeTorque = 3000.f;
}

UBeratWheelRear::UBeratWheelRear()
{
	WheelRadius = 33.f;
	WheelWidth = 22.f;
	bAffectedBySteering = false;
	MaxSteerAngle = 0.f;
	bAffectedByHandbrake = true;
	bAffectedByEngine = true;
	// more grip at the rear than the front, high slip / skid thresholds and a handbrake that slides rather than locks: the
	// template sports car's balance (equal grip and a locking handbrake spun the car and stopped it dead from 70 km/h)
	FrictionForceMultiplier = 3.6f;
	SlipThreshold = 100.f;
	SkidThreshold = 100.f;
	CorneringStiffness = 1000.f;
	SuspensionMaxRaise = 10.f;
	SuspensionMaxDrop = 10.f;
	SpringRate = 250.f;
	SpringPreload = 50.f;
	SuspensionDampingRatio = 0.5f;
	MaxBrakeTorque = 2000.f;
	MaxHandBrakeTorque = 2500.f;
}

void UBeratWheelAnimInstance::NativeUpdateAnimation(float DeltaSeconds)
{
	Super::NativeUpdateAnimation(DeltaSeconds);
	const AWheeledVehiclePawn* Pawn = Cast<AWheeledVehiclePawn>(GetOwningActor());
	const UChaosWheeledVehicleMovementComponent* M = Pawn ? Cast<UChaosWheeledVehicleMovementComponent>(Pawn->GetVehicleMovementComponent()) : nullptr;
	if (!M)
	{
		return;
	}
	Wheels.SetNum(M->WheelSetups.Num());
	for (int32 i = 0; i < Wheels.Num(); ++i)
	{
		const UChaosVehicleWheel* W = M->Wheels.IsValidIndex(i) ? M->Wheels[i].Get() : nullptr;
		Wheels[i].Bone = M->WheelSetups[i].BoneName;
		Wheels[i].SpinDeg = W ? W->GetRotationAngle() : 0.f;
		Wheels[i].SteerDeg = W ? W->GetSteerAngle() : 0.f;
		Wheels[i].LiftCm = W ? W->GetSuspensionOffset() : 0.f;
	}
}

void FBeratWheelProxy::PreUpdate(UAnimInstance* InAnimInstance, float DeltaSeconds)
{
	FAnimInstanceProxy::PreUpdate(InAnimInstance, DeltaSeconds);
	Wheels = CastChecked<UBeratWheelAnimInstance>(InAnimInstance)->Wheels;
}

bool FBeratWheelProxy::Evaluate(FPoseContext& Output)
{
	Output.ResetToRefPose();
	const FBoneContainer& Bones = Output.Pose.GetBoneContainer();
	for (const FBeratWheelPose& W : Wheels)
	{
		const int32 MeshIndex = Bones.GetPoseBoneIndexForBoneName(W.Bone);
		if (MeshIndex == INDEX_NONE)
		{
			continue;
		}
		const FCompactPoseBoneIndex CI = Bones.MakeCompactPoseIndex(FMeshPoseBoneIndex(MeshIndex));
		if (!CI.IsValid())
		{
			continue;
		}
		FTransform& T = Output.Pose[CI];
		// steering about the car's up axis, then the spin about the axle (Y), in the bone's parent space
		const FQuat Steer(FVector::UpVector, FMath::DegreesToRadians(W.SteerDeg));
		const FQuat Spin(FVector::RightVector, FMath::DegreesToRadians(-W.SpinDeg));
		T.SetRotation(Steer * Spin * T.GetRotation());
		T.AddToTranslation(FVector(0.f, 0.f, W.LiftCm));
	}
	return true;
}
