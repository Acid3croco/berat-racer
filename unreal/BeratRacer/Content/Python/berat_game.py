"""Game assets made from code: the car blueprints (each an ABeratCar taking a Chaos vehicle blueprint as its Template, with
its arcade-sim preset) and the game mode with the garage. Rerun after adding a car.

    import berat_game; berat_game.run()
"""

import unreal

ROOT = "/Game/Berat/Game"
eal = unreal.EditorAssetLibrary
assets = unreal.AssetToolsHelpers.get_asset_tools()

# name -> (Chaos vehicle blueprint to copy, display name, assist preset). The copy is reparented onto ABeratCar: it keeps the
# body and the whole Chaos setup of the original (built the normal way when it spawns) and gains the camera, lights, input
# and arcade-sim preset of ABeratCar.
CARS = {
    "BP_Car_Offroad": ("/Game/VehicleTemplate/Blueprints/OffroadCar/BP_VehicleAdvOffroadCar", "Off-road",
                       dict(steer_at_speed=0.40, countersteer=0.55, downforce_coefficient=0.3, yaw_from_steering=0.1,
                            air_levelling=0.8)),
    "BP_Car_Sport": ("/Game/VehicleTemplate/Blueprints/SportsCar/BP_VehicleAdvSportsCar", "Sport",
                     dict(steer_at_speed=0.30, countersteer=0.45, downforce_coefficient=0.9, yaw_from_steering=0.2)),
}


def make_blueprint(name, parent):
    path = f"{ROOT}/{name}"
    if eal.does_asset_exist(path):
        return unreal.load_asset(path)
    f = unreal.BlueprintFactory()
    f.set_editor_property("parent_class", parent)
    return assets.create_asset(name, ROOT, unreal.Blueprint, f)


def make_car(name, source):
    path = f"{ROOT}/{name}"
    if eal.does_asset_exist(path):
        eal.delete_asset(path)
    bp = eal.duplicate_asset(source, path)
    unreal.BlueprintEditorLibrary.reparent_blueprint(bp, unreal.BeratCar)
    return bp


def run():
    classes = []
    for name, (source, label, preset) in CARS.items():
        bp = make_car(name, source)
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        cdo = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}"))
        cdo.set_editor_property("display_name", label)
        a = cdo.get_editor_property("assists")
        for k, v in preset.items():
            a.set_editor_property(k, v)
        cdo.set_editor_property("assists", a)
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        eal.save_loaded_asset(bp)
        classes.append(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}"))
    gm = make_blueprint("BP_BeratGameMode", unreal.BeratGameMode)
    gcdo = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/BP_BeratGameMode"))
    gcdo.set_editor_property("cars", classes)
    unreal.BlueprintEditorLibrary.compile_blueprint(gm)
    eal.save_loaded_asset(gm)
    # the map uses it
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    ws = world.get_world_settings()
    ws.set_editor_property("default_game_mode", unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/BP_BeratGameMode"))
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    return [c.get_name() for c in classes]
