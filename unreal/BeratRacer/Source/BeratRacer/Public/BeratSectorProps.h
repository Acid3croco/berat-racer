#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "BeratSectorProps.generated.h"

class UHierarchicalInstancedStaticMeshComponent;
class UInstancedSkinnedMeshComponent;
class USkeletalMesh;

// The meshes one plant kind is drawn with: static (instanced static meshes) and / or skinned (Megaplants: Nanite skinned
// trees, instanced skinned meshes). Each plant takes one at random, stable per sector.
USTRUCT(BlueprintType)
struct FBeratKindMeshes
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") TArray<TObjectPtr<UStaticMesh>> Static;
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") TArray<TObjectPtr<USkeletalMesh>> Skinned;
	// cm; 0: the importer's default. Shrubs and hedges vanish long before trees.
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") float CullDistance = 0.f;
};
class UPointLightComponent;

// What one package sector holds besides the terrain, roads and buildings: plants (per kind, instanced) and street lamps.
// Filled by the importer; World Partition streams it with its sector.
UCLASS()
class BERATRACER_API ABeratSectorProps : public AActor
{
	GENERATED_BODY()

public:
	ABeratSectorProps();

	UPROPERTY(VisibleAnywhere, Category = "Berat") FIntPoint Sector = FIntPoint::ZeroValue;
	// Lamp heads: world position of the light (cm) in XYZ, W unused.
	UPROPERTY() TArray<FVector> LampHeads;

	// Add an instanced component for one mesh (the importer calls this per plant kind and for the lamp posts).
	UHierarchicalInstancedStaticMeshComponent* AddInstances(UStaticMesh* Mesh, FName Name, float CullDistance, bool bCollision);
	UInstancedSkinnedMeshComponent* AddSkinnedInstances(USkeletalMesh* Mesh, FName Name, float CullDistance);

protected:
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type Reason) override;
};

// Lamp registry of a world: the lamps of the loaded sectors.
UCLASS()
class BERATRACER_API UBeratLampSubsystem : public UWorldSubsystem
{
	GENERATED_BODY()

public:
	TArray<TWeakObjectPtr<ABeratSectorProps>> Sectors;
};

// A pool of real lights moved to the lamps nearest the camera; every other lamp glows through its emissive head only.
UCLASS()
class BERATRACER_API ABeratLampLights : public AActor
{
	GENERATED_BODY()

public:
	ABeratLampLights();

	UPROPERTY(EditAnywhere, Category = "Berat") int32 PoolSize = 64;
	UPROPERTY(EditAnywhere, Category = "Berat") float Candela = 900.f;      // ~25 lux under a 6 m lamp, a village LED street light
	UPROPERTY(EditAnywhere, Category = "Berat") float Radius = 2600.f;
	UPROPERTY(EditAnywhere, Category = "Berat") float ShadowedNearest = 4;     // the nearest lamps cast shadows
	UPROPERTY(EditAnywhere, Category = "Berat") FLinearColor Colour = FLinearColor(1.f, 0.78f, 0.5f);   // sodium-ish LED

	virtual void Tick(float DeltaSeconds) override;

protected:
	virtual void BeginPlay() override;

private:
	UPROPERTY(Transient) TArray<TObjectPtr<UPointLightComponent>> Pool;
	float Clock = 0.f;
};
