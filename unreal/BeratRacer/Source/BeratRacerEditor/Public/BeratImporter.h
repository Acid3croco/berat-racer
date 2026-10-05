#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "BeratImporter.generated.h"

class UMaterialInterface;
class UStaticMesh;
class ALandscape;
class UBeratLaneGraph;
class ABeratSectorProps;

// The C++ half of the importer: what is too big or too slow for the editor's Python (millions of samples and instances).
// Inputs are the files unreal/importer/prep.py and prep_objects.py write; the Python script (Content/Python/berat_import.py)
// drives these calls and does the rest (glTF imports, materials, lighting).
UCLASS()
class BERATRACEREDITOR_API UBeratImporter : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// Landscape from <BlockDir>/block.json, height.r16, layer_*.r8, holes.r8. One landscape layer per class, layer infos saved
	// under LayerInfoPath. Returns the landscape; OutSeconds the import time.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static ALandscape* ImportLandscape(const FString& BlockDir, UMaterialInterface* Material, const FString& LayerInfoPath,
		int32 WorldPartitionGridSize, double& OutSeconds);

	// Turn Nanite on for every landscape of the editor world and build its Nanite meshes (the terrain then stays within a
	// pixel of its full detail at every distance: no far LOD cutting through roads). Returns the seconds taken.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static double BuildLandscapeNanite();

	// Height of the landscape (or whatever blocks a downward trace) at a package point, in package metres; -1e9 if nothing.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static double TraceHeight(UObject* WorldContext, double X, double Y);

	// Lane graph asset from <BlockDir>/lanes.json.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static UBeratLaneGraph* ImportLaneGraph(const FString& LanesJson, const FString& AssetPath);

	// Props of one sector from <SectorDir>/plants.bin and lamps.bin. KindMeshes[k] is the mesh of plant kind k (see KINDS in
	// prep_objects.py), several meshes per kind allowed via KindVariants (k -> extra meshes); heights scale each mesh from
	// its own bounds. LampMesh is the lamp post (origin at its foot, arm along +X, head at LampHead).
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static ABeratSectorProps* ImportSectorProps(UObject* WorldContext, const FString& SectorDir, int32 Si, int32 Sj,
		const TArray<UStaticMesh*>& KindMeshes, const TMap<int32, UStaticMesh*>& KindVariants, UStaticMesh* LampMesh, FVector LampHead,
		float PlantCullDistance, int32& OutPlants, int32& OutLamps);
};
