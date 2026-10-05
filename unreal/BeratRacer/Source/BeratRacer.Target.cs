using UnrealBuildTool;

public class BeratRacerTarget : TargetRules
{
	public BeratRacerTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("BeratRacer");
	}
}
