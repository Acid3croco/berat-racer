using UnrealBuildTool;

public class BeratRacerEditor : ModuleRules
{
	public BeratRacerEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] {
			"Core", "CoreUObject", "Engine", "BeratRacer"
		});
		PrivateDependencyModuleNames.AddRange(new string[] {
			"UnrealEd", "Landscape", "LandscapeEditor", "Json", "JsonUtilities", "AssetTools"
		});
	}
}
