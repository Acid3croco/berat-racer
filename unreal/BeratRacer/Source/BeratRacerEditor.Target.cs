using UnrealBuildTool;

public class BeratRacerEditorTarget : TargetRules
{
	public BeratRacerEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.AddRange(new string[] { "BeratRacer", "BeratRacerEditor" });
	}
}
