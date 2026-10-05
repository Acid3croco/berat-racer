#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "BeratLaneGraph.generated.h"

UENUM()
enum class EBeratLaneKind : uint8 { Lane, Change, Connector, UTurn };

UENUM()
enum class EBeratControl : uint8 { Priority, GiveWay, Stop, Signals, Right };

// One element of the package's lane graph (lanes.json.gz), points in Unreal centimetres.
USTRUCT()
struct FBeratLane
{
	GENERATED_BODY()

	UPROPERTY() int64 Id = 0;
	UPROPERTY() EBeratLaneKind Kind = EBeratLaneKind::Lane;
	UPROPERTY() TArray<FVector> Points;
	UPROPERTY() TArray<float> Speed;          // m/s the curvature and the limit allow, per point
	UPROPERTY() TArray<float> Distance;       // cm from the first point, per point
	UPROPERTY() TArray<int32> Next;           // indices into the graph
	UPROPERTY() int32 Left = INDEX_NONE;
	UPROPERTY() int32 Right = INDEX_NONE;
	UPROPERTY() EBeratControl Control = EBeratControl::Priority;
	UPROPERTY() TArray<int32> Yields;         // connectors this one gives way to
	UPROPERTY() int32 Junction = INDEX_NONE;
	UPROPERTY() float Turn = 0.f;             // degrees, left positive
	UPROPERTY() float LimitKmh = 50.f;
	UPROPERTY() uint8 Importance = 5;
	UPROPERTY() bool bDirt = false;

	float Length() const { return Distance.Num() ? Distance.Last() : 0.f; }
};

USTRUCT()
struct FBeratLaneCell
{
	GENERATED_BODY()

	UPROPERTY() TArray<int32> Lanes;
};

UCLASS()
class BERATRACER_API UBeratLaneGraph : public UDataAsset
{
	GENERATED_BODY()

public:
	UPROPERTY() TArray<FBeratLane> Lanes;

	// Lanes whose first point lies in each 500 m cell, to find lanes near a point quickly.
	UPROPERTY() TMap<FIntPoint, FBeratLaneCell> Cells;

	static constexpr double CellSize = 50000.0;

	static FIntPoint CellOf(const FVector& P) { return FIntPoint(FMath::FloorToInt(P.X / CellSize), FMath::FloorToInt(P.Y / CellSize)); }

	// Position and unit direction at distance D (cm) along lane L.
	void Sample(int32 L, float D, FVector& OutPos, FVector& OutDir) const;

	// Rebuild Distance and Cells from Points (after an import).
	void Finalize();
};
