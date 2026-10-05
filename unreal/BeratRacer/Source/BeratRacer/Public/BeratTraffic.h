#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "BeratTraffic.generated.h"

class UBeratLaneGraph;
class UStaticMeshComponent;
class UStaticMesh;
class USpotLightComponent;

// One traffic car: kinematic, it follows the lane graph (no physics simulation), with a box the player can hit.
USTRUCT()
struct FBeratTrafficCar
{
	GENERATED_BODY()

	int32 Lane = INDEX_NONE;
	float S = 0.f;               // cm along the lane
	float Speed = 0.f;           // cm/s
	float Desired = 1.f;         // driver's factor on the allowed speed (0.85 .. 1.1)
	int32 NextLane = INDEX_NONE; // chosen at the end of the lane
	int32 Model = 0;
	float Length = 450.f;
	float Stopped = 0.f;         // seconds held at a stop line
	bool bActive = false;
	UPROPERTY() TObjectPtr<UStaticMeshComponent> Body;
};

// Traffic around the player on the package's lane graph. Car following is the Intelligent Driver Model; junctions follow the
// graph's controls: a connector waits while a connector it yields to is occupied, stop lines are held 1.5 s, signals cycle per
// junction. Cars appear out of sight 250..900 m away and vanish beyond 1.1 km.
UCLASS()
class BERATRACER_API ABeratTraffic : public AActor
{
	GENERATED_BODY()

public:
	ABeratTraffic();

	UPROPERTY(EditAnywhere, Category = "Traffic") TObjectPtr<UBeratLaneGraph> Graph;
	// Body meshes (static meshes of the traffic cars), picked at random per car.
	UPROPERTY(EditAnywhere, Category = "Traffic") TArray<TObjectPtr<UStaticMesh>> Models;
	UPROPERTY(EditAnywhere, Category = "Traffic") int32 MaxCars = 160;
	UPROPERTY(EditAnywhere, Category = "Traffic") float SpawnMin = 25000.f;
	UPROPERTY(EditAnywhere, Category = "Traffic") float SpawnMax = 90000.f;
	UPROPERTY(EditAnywhere, Category = "Traffic") float DespawnAt = 110000.f;
	// Cars per km of lane, by road importance 1 (motorway) .. 5 (local), 6 (track: none).
	UPROPERTY(EditAnywhere, Category = "Traffic") TArray<float> DensityPerKm = {6.f, 5.f, 3.5f, 2.f, 0.8f, 0.f};

	virtual void Tick(float DeltaSeconds) override;

protected:
	virtual void BeginPlay() override;

private:
	UPROPERTY(Transient) TArray<FBeratTrafficCar> Cars;

	// Occupancy: lane index -> cars on it (indices into Cars), rebuilt every tick.
	TMap<int32, TArray<int32>> OnLane;

	FVector ViewPos = FVector::ZeroVector;
	FVector ViewDir = FVector::ForwardVector;
	float SpawnClock = 0.f;
	double Time = 0.0;

	void SpawnSome();
	bool TrySpawn(int32 Car);
	int32 PickNext(int32 Lane) const;
	float GapAhead(int32 Car, float& OutLeaderSpeed) const;
	float StopDistance(int32 Car) const;
	bool SignalGreen(const struct FBeratLane& Connector) const;
	void Place(FBeratTrafficCar& C);
};
