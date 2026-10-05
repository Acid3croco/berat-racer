using UnrealBuildTool;

public class BeratRacer : ModuleRules
{
	public BeratRacer(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] {
			"Core", "CoreUObject", "Engine", "InputCore", "EnhancedInput",
			"ChaosVehicles", "ChaosVehiclesCore", "PhysicsCore", "Json", "JsonUtilities", "SunPosition", "AnimGraphRuntime", "Niagara", "RenderCore", "AudioMixer"
		});
	}
}
