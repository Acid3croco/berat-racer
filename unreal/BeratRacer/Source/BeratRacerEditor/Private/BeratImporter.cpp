#include "BeratImporter.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BeratLaneGraph.h"
#include "BeratSectorProps.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/InstancedSkinnedMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/StaticMesh.h"
#include "Engine/SkeletalMesh.h"
#include "MeshUtilities.h"
#include "Engine/NaniteAssemblyData.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/World.h"
#include "HAL/PlatformTime.h"
#include "Landscape.h"
#include "LandscapeConfigHelper.h"
#include "LandscapeEdit.h"
#include "LandscapeImportHelper.h"
#include "LandscapeInfo.h"
#include "LandscapeLayerInfoObject.h"
#include "LandscapeSubsystem.h"
#include "LandscapeEditTypes.h"
#include "EngineUtils.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogBerat, Log, All);

namespace
{
	TSharedPtr<FJsonObject> ReadJson(const FString& Path)
	{
		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			UE_LOG(LogBerat, Error, TEXT("cannot read %s"), *Path);
			return nullptr;
		}
		TSharedPtr<FJsonObject> Obj;
		FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Obj);
		return Obj;
	}

	bool ReadRaw(const FString& Path, TArray<uint8>& Out, int64 Expected)
	{
		if (!FFileHelper::LoadFileToArray(Out, *Path) || Out.Num() != Expected)
		{
			UE_LOG(LogBerat, Error, TEXT("%s: %d bytes, expected %lld"), *Path, Out.Num(), Expected);
			return false;
		}
		return true;
	}

	template <typename T>
	T* CreateAsset(const FString& PackagePath, UClass* Class = T::StaticClass())
	{
		const FString Name = FPaths::GetBaseFilename(PackagePath);
		UPackage* Pkg = CreatePackage(*PackagePath);
		Pkg->FullyLoad();
		T* Obj = FindObject<T>(Pkg, *Name);
		if (!Obj)
		{
			Obj = NewObject<T>(Pkg, Class, *Name, RF_Public | RF_Standalone | RF_Transactional);
			FAssetRegistryModule::AssetCreated(Obj);
		}
		Obj->MarkPackageDirty();
		return Obj;
	}

	void SaveAsset(UObject* Obj)
	{
		UPackage* Pkg = Obj->GetOutermost();
		const FString File = FPackageName::LongPackageNameToFilename(Pkg->GetName(), FPackageName::GetAssetPackageExtension());
		FSavePackageArgs Args;
		Args.TopLevelFlags = RF_Public | RF_Standalone;
		UPackage::SavePackage(Pkg, Obj, *File, Args);
	}
}

ALandscape* UBeratImporter::ImportLandscape(const FString& BlockDir, UMaterialInterface* Material, const FString& LayerInfoPath,
	int32 WorldPartitionGridSize, double& OutSeconds)
{
	const double T0 = FPlatformTime::Seconds();
	UWorld* World = GEditor->GetEditorWorldContext().World();
	TSharedPtr<FJsonObject> Block = ReadJson(BlockDir / TEXT("block.json"));
	if (!Block || !World)
	{
		return nullptr;
	}
	const TArray<TSharedPtr<FJsonValue>>& Size = Block->GetArrayField(TEXT("size"));
	const int32 W = int32(Size[0]->AsNumber()), H = int32(Size[1]->AsNumber());
	const int32 Quads = Block->GetIntegerField(TEXT("quads_per_section"));
	const int32 Sections = Block->GetIntegerField(TEXT("sections_per_component"));
	const TArray<TSharedPtr<FJsonValue>>& Loc = Block->GetArrayField(TEXT("location_cm"));
	const TArray<TSharedPtr<FJsonValue>>& Scl = Block->GetArrayField(TEXT("scale_cm"));

	// Heights: the package's 16-bit values, unchanged (the transform's Z scale and offset map them to metres NGF).
	TArray<uint8> Raw;
	if (!ReadRaw(BlockDir / TEXT("height.r16"), Raw, int64(W) * H * 2))
	{
		return nullptr;
	}
	TArray<uint16> Heights;
	Heights.SetNumUninitialized(W * H);
	FMemory::Memcpy(Heights.GetData(), Raw.GetData(), Raw.Num());

	// Layers: one per class, weights from prep.py (soft edges, summing to 255).
	TArray<FLandscapeImportLayerInfo> Layers;
	for (const TSharedPtr<FJsonValue>& V : Block->GetArrayField(TEXT("layers")))
	{
		const FString Name = V->AsObject()->GetStringField(TEXT("name"));
		FLandscapeImportLayerInfo Info;
		Info.LayerName = FName(*Name);
		ULandscapeLayerInfoObject* LI = CreateAsset<ULandscapeLayerInfoObject>(LayerInfoPath / (TEXT("LI_") + Name));
		LI->SetLayerName(Info.LayerName, false);
		SaveAsset(LI);
		Info.LayerInfo = LI;
		if (!ReadRaw(BlockDir / FString::Printf(TEXT("layer_%s.r8"), *Name), Info.LayerData, int64(W) * H))
		{
			return nullptr;
		}
		Layers.Add(MoveTemp(Info));
	}

	TMap<FGuid, TArray<uint16>> HeightPerLayer;
	TMap<FGuid, TArray<FLandscapeImportLayerInfo>> MaterialPerLayer;
	HeightPerLayer.Add(FGuid(), MoveTemp(Heights));
	MaterialPerLayer.Add(FGuid(), MoveTemp(Layers));

	// one landscape per block or region, named after its folder (b3x3, r2_3...)
	const FString Label = TEXT("Landscape_") + FPaths::GetCleanFilename(FPaths::GetPath(BlockDir / TEXT("block.json")));
	FActorSpawnParameters P;
	P.Name = FName(*Label);
	P.NameMode = FActorSpawnParameters::ESpawnActorNameMode::Requested;
	const FVector Location(Loc[0]->AsNumber(), Loc[1]->AsNumber(), Loc[2]->AsNumber());
	ALandscape* Land = World->SpawnActor<ALandscape>(Location, FRotator::ZeroRotator, P);
	Land->SetActorRelativeScale3D(FVector(Scl[0]->AsNumber(), Scl[1]->AsNumber(), Scl[2]->AsNumber()));
	Land->LandscapeMaterial = Material;
	Land->Import(FGuid::NewGuid(), 0, 0, W - 1, H - 1, Sections, Quads, HeightPerLayer, nullptr, MaterialPerLayer,
		ELandscapeImportAlphamapType::Additive, TArrayView<const FLandscapeLayer>());
	Land->SetActorLabel(Label);

	// Visibility (holes): painted after the import through the landscape edit interface.
	TArray<uint8> Holes;
	if (ReadRaw(BlockDir / TEXT("holes.r8"), Holes, int64(W) * H) && Block->GetIntegerField(TEXT("holes")) > 0)
	{
		ULandscapeInfo* Info = Land->GetLandscapeInfo();
		FLandscapeEditDataInterface Edit(Info);
		Edit.SetAlphaData(ALandscapeProxy::VisibilityLayer, 0, 0, W - 1, H - 1, Holes.GetData(), 0, ELandscapeLayerPaintingRestriction::None);
		Edit.Flush();
	}

	// World Partition: split into streaming proxies of GridSize components.
	if (WorldPartitionGridSize > 0 && World->GetWorldPartition())
	{
		ULandscapeInfo* Info = Land->GetLandscapeInfo();
		TSet<AActor*> Modified;
		FLandscapeConfigHelper::ChangeGridSize(Info, WorldPartitionGridSize, Modified);
	}
	OutSeconds = FPlatformTime::Seconds() - T0;
	UE_LOG(LogBerat, Display, TEXT("landscape %dx%d imported in %.1f s"), W, H, OutSeconds);
	return Land;
}

double UBeratImporter::BuildLandscapeNanite()
{
	const double T0 = FPlatformTime::Seconds();
	UWorld* World = GEditor->GetEditorWorldContext().World();
	for (TActorIterator<ALandscape> It(World); It; ++It)
	{
		if (!It->IsNaniteEnabled())
		{
			It->Modify();
			FProperty* P = ALandscapeProxy::StaticClass()->FindPropertyByName(TEXT("bEnableNanite"));
			It->PreEditChange(P);
			CastField<FBoolProperty>(P)->SetPropertyValue_InContainer(*It, true);
			FPropertyChangedEvent E(P);
			It->PostEditChangeProperty(E);
		}
	}
	if (ULandscapeSubsystem* S = World->GetSubsystem<ULandscapeSubsystem>())
	{
		S->BuildNanite(UE::Landscape::EBuildFlags::WriteFinalLog | UE::Landscape::EBuildFlags::ForceRebuild);
	}
	const double Dt = FPlatformTime::Seconds() - T0;
	UE_LOG(LogBerat, Display, TEXT("landscape Nanite built in %.1f s"), Dt);
	return Dt;
}

UStaticMesh* UBeratImporter::BakeSkeletalToStatic(USkeletalMesh* Mesh, const FString& PackagePath)
{
	if (!Mesh)
	{
		return nullptr;
	}
	UWorld* World = GEditor->GetEditorWorldContext().World();
	USkeletalMeshComponent* C = NewObject<USkeletalMeshComponent>(GetTransientPackage());
	C->SetSkeletalMeshAsset(Mesh);
	C->RegisterComponentWithWorld(World);
	C->RefreshBoneTransforms();
	IMeshUtilities& MU = FModuleManager::Get().LoadModuleChecked<IMeshUtilities>("MeshUtilities");
	TArray<UMeshComponent*> Comps = {C};
	UStaticMesh* SM = MU.ConvertMeshesToStaticMesh(Comps, FTransform::Identity, PackagePath);
	C->UnregisterComponent();
	C->DestroyComponent();
	if (SM)
	{
		FMeshNaniteSettings N = SM->GetNaniteSettings();
		N.bEnabled = true;
		N.FallbackTarget = ENaniteFallbackTarget::RelativeError;
		// The skeletal mesh's Nanite Assembly (Megaplants: the leafy twigs and branches instanced on the bones) carried over
		// as a static assembly: same parts, node transforms made local through the skeleton's reference pose.
		const FNaniteAssemblyData& Src = Mesh->GetNaniteSettings().NaniteAssemblyData;
		if (Src.IsValid())
		{
			const FReferenceSkeleton& R = Mesh->GetRefSkeleton();
			TArray<FTransform> Comp;
			Comp.SetNum(R.GetNum());
			for (int32 i = 0; i < R.GetNum(); ++i)
			{
				const int32 P = R.GetParentIndex(i);
				Comp[i] = P == INDEX_NONE ? R.GetRefBonePose()[i] : R.GetRefBonePose()[i] * Comp[P];
			}
			FNaniteAssemblyData Dst;
			Dst.Parts = Src.Parts;
			// a static assembly needs static parts: skeletal parts (Megaplants twigs and branches) baked once, next to the tree
			for (FNaniteAssemblyPart& Part : Dst.Parts)
			{
				USkeletalMesh* SkPart = Cast<USkeletalMesh>(Part.MeshObjectPath.TryLoad());
				if (!SkPart)
				{
					continue;
				}
				const FString PartPath = FPaths::GetPath(PackagePath) / TEXT("Parts") / (TEXT("SM_") + SkPart->GetName());
				UStaticMesh* Baked = LoadObject<UStaticMesh>(nullptr, *(PartPath + TEXT(".") + FPaths::GetBaseFilename(PartPath)));
				if (!Baked)
				{
					Baked = BakeSkeletalToStatic(SkPart, PartPath);
				}
				if (Baked)
				{
					Part.MeshObjectPath = FSoftObjectPath(Baked);
				}
			}
			Dst.Nodes.Reserve(Src.Nodes.Num());
			for (const FNaniteAssemblyNode& In : Src.Nodes)
			{
				FNaniteAssemblyNode Out;
				Out.PartIndex = In.PartIndex;
				Out.TransformSpace = ENaniteAssemblyNodeTransformSpace::Local;
				Out.Transform = In.Transform;
				if (In.TransformSpace == ENaniteAssemblyNodeTransformSpace::BoneRelative && In.BoneInfluences.Num() > 0
					&& Comp.IsValidIndex(In.BoneInfluences[0].BoneIndex))
				{
					Out.Transform = In.Transform * FTransform3f(Comp[In.BoneInfluences[0].BoneIndex]);
				}
				Dst.Nodes.Add(Out);
			}
			N.NaniteAssemblyData = MoveTemp(Dst);
			UE_LOG(LogBerat, Display, TEXT("%s: assembly of %d parts, %d nodes carried over"), *Mesh->GetName(), Src.Parts.Num(), Src.Nodes.Num());
		}
		SM->SetNaniteSettings(N);
		SM->Build(false);
		SM->MarkPackageDirty();
		SaveAsset(SM);
	}
	return SM;
}

TArray<FString> UBeratImporter::GetBoneNames(USkeletalMesh* Mesh)
{
	TArray<FString> Out;
	if (!Mesh)
	{
		return Out;
	}
	const FReferenceSkeleton& R = Mesh->GetRefSkeleton();
	TArray<FTransform> Comp;
	Comp.SetNum(R.GetNum());
	for (int32 i = 0; i < R.GetNum(); ++i)
	{
		const int32 P = R.GetParentIndex(i);
		Comp[i] = P == INDEX_NONE ? R.GetRefBonePose()[i] : R.GetRefBonePose()[i] * Comp[P];
		const FVector L = Comp[i].GetLocation();
		Out.Add(FString::Printf(TEXT("%s %.1f %.1f %.1f"), *R.GetBoneName(i).ToString(), L.X, L.Y, L.Z));
	}
	return Out;
}

double UBeratImporter::TraceHeight(UObject* WorldContext, double X, double Y)
{
	UWorld* World = WorldContext ? WorldContext->GetWorld() : GEditor->GetEditorWorldContext().World();
	FHitResult Hit;
	const FVector A(X * 100.0, -Y * 100.0, 200000.0), B(X * 100.0, -Y * 100.0, -10000.0);
	FCollisionQueryParams Q;
	Q.bTraceComplex = true;
	if (World && World->LineTraceSingleByChannel(Hit, A, B, ECC_WorldStatic, Q))
	{
		return Hit.ImpactPoint.Z / 100.0;
	}
	return -1e9;
}

UBeratLaneGraph* UBeratImporter::ImportLaneGraphBin(const TArray<FString>& Files, const FString& AssetPath)
{
	const double T0 = FPlatformTime::Seconds();
	struct FRaw { TArray<int64> Next, Yields; int64 Left = -1, Right = -1; };
	TArray<FBeratLane> Lanes;
	TArray<FRaw> Raw;
	for (const FString& F : Files)
	{
		TArray<uint8> Bytes;
		if (!FFileHelper::LoadFileToArray(Bytes, *F) || Bytes.Num() < 4)
		{
			continue;
		}
		int64 Pos = 0;
		auto Read = [&](void* Dst, int32 N) { FMemory::Memcpy(Dst, Bytes.GetData() + Pos, N); Pos += N; };
		int32 Count = 0; Read(&Count, 4);
		for (int32 k = 0; k < Count && Pos < Bytes.Num(); ++k)
		{
			FBeratLane L; FRaw R;
			int64 Id; uint8 Kind, Ctl, Imp, Dirt; int32 Jn; float Turn, Limit; uint16 N;
			Read(&Id, 8); Read(&Kind, 1); Read(&Ctl, 1); Read(&Imp, 1); Read(&Dirt, 1); Read(&Jn, 4); Read(&Turn, 4); Read(&Limit, 4);
			Read(&N, 2);
			L.Id = Id; L.Kind = EBeratLaneKind(FMath::Min<uint8>(Kind, 3)); L.Control = EBeratControl(FMath::Min<uint8>(Ctl, 4));
			L.Importance = FMath::Clamp<uint8>(Imp, 1, 6); L.bDirt = Dirt != 0; L.Junction = Jn; L.Turn = Turn; L.LimitKmh = Limit;
			L.Points.SetNumUninitialized(N); L.Speed.SetNumUninitialized(N);
			for (int32 i = 0; i < N; ++i)
			{
				float V[4]; Read(V, 16);
				L.Points[i] = FVector(V[0], V[1], V[2]); L.Speed[i] = V[3];
			}
			uint8 NN; Read(&NN, 1); R.Next.SetNumUninitialized(NN); if (NN) Read(R.Next.GetData(), 8 * NN);
			Read(&R.Left, 8); Read(&R.Right, 8);
			uint8 NY; Read(&NY, 1); R.Yields.SetNumUninitialized(NY); if (NY) Read(R.Yields.GetData(), 8 * NY);
			Lanes.Add(MoveTemp(L)); Raw.Add(MoveTemp(R));
		}
	}
	TMap<int64, int32> Index;
	Index.Reserve(Lanes.Num());
	for (int32 i = 0; i < Lanes.Num(); ++i)
	{
		Index.Add(Lanes[i].Id, i);
	}
	auto Ref = [&](int64 Id) { const int32* I = Index.Find(Id); return I ? *I : INDEX_NONE; };
	for (int32 i = 0; i < Lanes.Num(); ++i)
	{
		for (int64 N : Raw[i].Next) { const int32 R = Ref(N); if (R != INDEX_NONE) Lanes[i].Next.Add(R); }
		for (int64 Y : Raw[i].Yields) { const int32 R = Ref(Y); if (R != INDEX_NONE) Lanes[i].Yields.Add(R); }
		Lanes[i].Left = Ref(Raw[i].Left);
		Lanes[i].Right = Ref(Raw[i].Right);
	}
	UBeratLaneGraph* G = CreateAsset<UBeratLaneGraph>(AssetPath);
	G->Lanes = MoveTemp(Lanes);
	G->Finalize();
	SaveAsset(G);
	UE_LOG(LogBerat, Display, TEXT("lane graph (binary): %d lanes from %d files, %d cells, %.1f s"), G->Lanes.Num(), Files.Num(),
		G->Cells.Num(), FPlatformTime::Seconds() - T0);
	return G;
}

UBeratLaneGraph* UBeratImporter::ImportLaneGraph(const FString& LanesJson, const FString& AssetPath)
{
	TSharedPtr<FJsonObject> Root = ReadJson(LanesJson);
	if (!Root)
	{
		return nullptr;
	}
	TArray<FString> Controls;
	for (const TSharedPtr<FJsonValue>& V : Root->GetArrayField(TEXT("controls")))
	{
		Controls.Add(V->AsString());
	}
	auto ControlOf = [&](int32 I)
	{
		const FString C = Controls.IsValidIndex(I) ? Controls[I] : TEXT("priority");
		return C == TEXT("give_way") ? EBeratControl::GiveWay : C == TEXT("stop") ? EBeratControl::Stop
			: C == TEXT("signals") ? EBeratControl::Signals : C == TEXT("right") ? EBeratControl::Right : EBeratControl::Priority;
	};

	UBeratLaneGraph* G = CreateAsset<UBeratLaneGraph>(AssetPath);
	G->Lanes.Reset();
	const TArray<TSharedPtr<FJsonValue>>& Elems = Root->GetArrayField(TEXT("elements"));
	TMap<int64, int32> Index;
	Index.Reserve(Elems.Num());
	for (const TSharedPtr<FJsonValue>& V : Elems)
	{
		Index.Add(int64(V->AsObject()->GetNumberField(TEXT("id"))), Index.Num());
	}
	auto Ref = [&](double Id) { const int32* I = Index.Find(int64(Id)); return I ? *I : INDEX_NONE; };
	G->Lanes.SetNum(Elems.Num());
	for (int32 i = 0; i < Elems.Num(); ++i)
	{
		const TSharedPtr<FJsonObject>& E = Elems[i]->AsObject();
		FBeratLane& L = G->Lanes[i];
		L.Id = int64(E->GetNumberField(TEXT("id")));
		L.Kind = EBeratLaneKind(FMath::Clamp(int32(E->GetNumberField(TEXT("kind"))), 0, 3));
		for (const TSharedPtr<FJsonValue>& P : E->GetArrayField(TEXT("points")))
		{
			const TArray<TSharedPtr<FJsonValue>>& C = P->AsArray();
			L.Points.Add(FVector(C[0]->AsNumber(), C[1]->AsNumber(), C[2]->AsNumber()));
		}
		for (const TSharedPtr<FJsonValue>& S : E->GetArrayField(TEXT("speed")))
		{
			L.Speed.Add(float(S->AsNumber()));
		}
		for (const TSharedPtr<FJsonValue>& N : E->GetArrayField(TEXT("next")))
		{
			const int32 R = Ref(N->AsNumber());
			if (R != INDEX_NONE)
			{
				L.Next.Add(R);
			}
		}
		for (const TSharedPtr<FJsonValue>& Y : E->GetArrayField(TEXT("yields")))
		{
			const int32 R = Ref(Y->AsNumber());
			if (R != INDEX_NONE)
			{
				L.Yields.Add(R);
			}
		}
		L.Left = Ref(E->GetNumberField(TEXT("left")));
		L.Right = Ref(E->GetNumberField(TEXT("right")));
		double Ctl = 0, Jn = -1, Turn = 0, Limit = 50;
		E->TryGetNumberField(TEXT("control"), Ctl);
		E->TryGetNumberField(TEXT("junction"), Jn);
		E->TryGetNumberField(TEXT("turn"), Turn);
		E->TryGetNumberField(TEXT("limit"), Limit);
		L.Control = ControlOf(int32(Ctl));
		L.Junction = int32(Jn);
		L.Turn = float(Turn);
		L.LimitKmh = float(Limit);
		const TSharedPtr<FJsonObject>* Road;
		if (E->TryGetObjectField(TEXT("road"), Road))
		{
			double Imp = 5;
			bool bDirt = false;
			(*Road)->TryGetNumberField(TEXT("importance"), Imp);
			(*Road)->TryGetBoolField(TEXT("dirt"), bDirt);
			L.Importance = uint8(FMath::Clamp(int32(Imp), 1, 6));
			L.bDirt = bDirt;
		}
	}
	G->Finalize();
	SaveAsset(G);
	UE_LOG(LogBerat, Display, TEXT("lane graph: %d lanes, %d cells"), G->Lanes.Num(), G->Cells.Num());
	return G;
}

ABeratSectorProps* UBeratImporter::ImportSectorProps(UObject* WorldContext, const FString& SectorDir, int32 Si, int32 Sj,
	const TArray<FBeratKindMeshes>& Kinds, UStaticMesh* LampMesh, FVector LampHead,
	float PlantCullDistance, int32& OutPlants, int32& OutLamps)
{
	OutPlants = OutLamps = 0;
	UWorld* World = WorldContext ? WorldContext->GetWorld() : GEditor->GetEditorWorldContext().World();
	const FString Label = FString::Printf(TEXT("Props_%d_%d"), Si, Sj);
	FActorSpawnParameters P;
	P.Name = FName(*Label);
	P.NameMode = FActorSpawnParameters::ESpawnActorNameMode::Requested;
	// The actor sits at the sector's centre so World Partition streams it with its sector.
	const double Cx = 3200.0 * Si - 16000.0 + 1600.0, Cy = 3200.0 * Sj - 16000.0 + 1600.0;
	ABeratSectorProps* A = World->SpawnActor<ABeratSectorProps>(FVector(Cx * 100.0, -Cy * 100.0, 0.0), FRotator::ZeroRotator, P);
	A->SetActorLabel(Label);
	A->Sector = FIntPoint(Si, Sj);
	const FTransform ToLocal = A->GetActorTransform().Inverse();

	// plants.bin: int32 count, then (float x, y, z, height, yaw, int32 kind), Unreal cm.
	TArray<uint8> Raw;
	if (FFileHelper::LoadFileToArray(Raw, *(SectorDir / TEXT("plants.bin"))) && Raw.Num() >= 4)
	{
		const int32 N = *reinterpret_cast<const int32*>(Raw.GetData());
		struct FRec { float X, Y, Z, H, Yaw; int32 Kind; };
		const FRec* Rec = reinterpret_cast<const FRec*>(Raw.GetData() + 4);
		TMap<UObject*, TArray<FTransform>> Xf;
		TMap<UObject*, float> Cull;
		FRandomStream Rng(Si * 1000 + Sj);
		for (int32 i = 0; i < N; ++i)
		{
			const FRec& R = Rec[i];
			if (!Kinds.IsValidIndex(R.Kind))
			{
				continue;
			}
			const FBeratKindMeshes& K = Kinds[R.Kind];
			const int32 Total = K.Static.Num() + K.Skinned.Num();
			if (Total == 0)
			{
				continue;
			}
			const int32 Pick = Rng.RandRange(0, Total - 1);
			UObject* M = Pick < K.Static.Num() ? static_cast<UObject*>(K.Static[Pick].Get()) : static_cast<UObject*>(K.Skinned[Pick - K.Static.Num()].Get());
			if (!M)
			{
				continue;
			}
			// height of the model: from its base (the origin) to the top of its bounds
			float MeshH = 100.f;
			if (const UStaticMesh* SM = Cast<UStaticMesh>(M))
			{
				const FBoxSphereBounds B = SM->GetBounds();
				MeshH = B.Origin.Z + B.BoxExtent.Z;
			}
			else if (const USkeletalMesh* SK = Cast<USkeletalMesh>(M))
			{
				const FBoxSphereBounds B = SK->GetBounds();
				MeshH = B.Origin.Z + B.BoxExtent.Z;
			}
			const float S = R.H / FMath::Max(MeshH, 1.f);
			const float Wid = S * Rng.FRandRange(0.9f, 1.1f);
			const FTransform T(FRotator(0, R.Yaw, 0), FVector(R.X, R.Y, R.Z - 10.f), FVector(Wid, Wid, S));
			Xf.FindOrAdd(M).Add(T * ToLocal);
			if (K.CullDistance > 0.f)
			{
				Cull.FindOrAdd(M) = K.CullDistance;
			}
		}
		for (auto& [Mesh, Ts] : Xf)
		{
			if (UStaticMesh* SM = Cast<UStaticMesh>(Mesh))
			{
				UHierarchicalInstancedStaticMeshComponent* C = A->AddInstances(SM, *FString::Printf(TEXT("HISM_%s"), *SM->GetName()),
					Cull.Contains(Mesh) ? Cull[Mesh] : PlantCullDistance, false);
				C->AddInstances(Ts, false);
			}
			else if (USkeletalMesh* SK = Cast<USkeletalMesh>(Mesh))
			{
				UInstancedSkinnedMeshComponent* C = A->AddSkinnedInstances(SK, *FString::Printf(TEXT("ISKM_%s"), *SK->GetName()),
					Cull.Contains(Mesh) ? Cull[Mesh] : PlantCullDistance);
				TArray<int32> Anim;
				Anim.Init(0, Ts.Num());
				C->AddInstances(Ts, Anim, false);
			}
			OutPlants += Ts.Num();
		}
	}

	// lamps.bin: int32 count, then (float x, y, z, yaw), Unreal cm.
	if (LampMesh && FFileHelper::LoadFileToArray(Raw, *(SectorDir / TEXT("lamps.bin"))) && Raw.Num() >= 4)
	{
		const int32 N = *reinterpret_cast<const int32*>(Raw.GetData());
		const float* F = reinterpret_cast<const float*>(Raw.GetData() + 4);
		TArray<FTransform> Ts;
		for (int32 i = 0; i < N; ++i)
		{
			const FVector Foot(F[4 * i], F[4 * i + 1], F[4 * i + 2]);
			const FRotator Rot(0, F[4 * i + 3], 0);
			Ts.Add(FTransform(Rot, Foot) * ToLocal);
			A->LampHeads.Add(Foot + Rot.RotateVector(LampHead));
		}
		UHierarchicalInstancedStaticMeshComponent* C = A->AddInstances(LampMesh, TEXT("HISM_Lamps"), 150000.f, true);
		C->AddInstances(Ts, false);
		OutLamps = N;
	}
	return A;
}
