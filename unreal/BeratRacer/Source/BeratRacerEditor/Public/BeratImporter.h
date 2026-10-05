#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "BeratSectorProps.h"
#include "BeratImporter.generated.h"

class UMaterialInterface;
class UStaticMesh;
class USkeletalMesh;
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

	// A static Nanite copy of a skeletal mesh (its reference pose), saved at PackagePath: Megaplants trees are skinned Nanite
	// meshes, and 330k skinned instances ran at 3 fps; static instances do not pay for the skeleton.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static UStaticMesh* BakeSkeletalToStatic(USkeletalMesh* Mesh, const FString& PackagePath);

	// Bones of a skeletal mesh: "name x y z" (reference pose, component space, cm), to find wheel bones from a script.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static TArray<FString> GetBoneNames(USkeletalMesh* Mesh);

	// Height of the landscape (or whatever blocks a downward trace) at a package point, in package metres; -1e9 if nothing.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static double TraceHeight(UObject* WorldContext, double X, double Y);

	// Lane graph asset from the sectors' lanes.bin (prep_objects.py); links across files resolved by id.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static UBeratLaneGraph* ImportLaneGraphBin(const TArray<FString>& Files, const FString& AssetPath);

	// Lane graph asset from <BlockDir>/lanes.json.
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static UBeratLaneGraph* ImportLaneGraph(const FString& LanesJson, const FString& AssetPath);

	// Props of one sector from <SectorDir>/plants.bin and lamps.bin. KindMeshes[k] is the mesh of plant kind k (see KINDS in
	// prep_objects.py), several meshes per kind allowed via KindVariants (k -> extra meshes); heights scale each mesh from
	// its own bounds. LampMesh is the lamp post (origin at its foot, arm along +X, head at LampHead).
	UFUNCTION(BlueprintCallable, Category = "Berat|Import")
	static ABeratSectorProps* ImportSectorProps(UObject* WorldContext, const FString& SectorDir, int32 Si, int32 Sj,
		const TArray<FBeratKindMeshes>& Kinds, UStaticMesh* LampMesh, FVector LampHead,
		float PlantCullDistance, int32& OutPlants, int32& OutLamps);

	// Reflection helpers for assets Python cannot reach (Niagara lightweight emitters: their modules and renderers are
	// UObjects behind engine-internal headers). Objects of a Niagara system: each stateless emitter, then its modules,
	// then its renderers.
	UFUNCTION(BlueprintCallable, Category = "Berat|Edit")
	static TArray<UObject*> NiagaraStatelessObjects(UObject* System);

	// Property names of an object (with their C++ type), a property as exported text, a property set from text.
	UFUNCTION(BlueprintCallable, Category = "Berat|Edit")
	static TArray<FString> PropertyNames(UObject* Object);
	UFUNCTION(BlueprintCallable, Category = "Berat|Edit")
	static FString GetPropertyText(UObject* Object, const FString& Name);
	UFUNCTION(BlueprintCallable, Category = "Berat|Edit")
	static bool SetPropertyText(UObject* Object, const FString& Name, const FString& Text);
	// PostEditChange on each (rebuilds a Niagara emitter's data), then marks the package dirty.
	UFUNCTION(BlueprintCallable, Category = "Berat|Edit")
	static void NotifyChanged(const TArray<UObject*>& Objects);
};
