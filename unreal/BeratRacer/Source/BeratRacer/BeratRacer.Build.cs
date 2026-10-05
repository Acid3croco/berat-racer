using UnrealBuildTool;

public class BeratRacer : ModuleRules
{
	public BeratRacer(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] {
			"Core", "CoreUObject", "Engine", "InputCore", "EnhancedInput",
			"ChaosVehicles", "PhysicsCore", "Json", "JsonUtilities"
		});
	}
}
