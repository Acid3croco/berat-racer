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
# name -> how the car is made, display name, assist preset.
#   ("copy", blueprint): a Chaos vehicle blueprint, copied and reparented onto ABeratCar
#   ("mesh", spec): built here from a skeletal mesh (the Vehicle Variety Pack's UE4 PhysX blueprints do not load in UE5):
#                   wheel bones and radius, drive, torque, mass; engine curve and gearbox from Epic's template sports car
VVP = "/Game/VehicleVarietyPack/Skeletons"
WHEELS = ("Wheel_Front_Left", "Wheel_Front_Right", "Wheel_Rear_Left", "Wheel_Rear_Right")
CARS = {
    "BP_Car_Hatchback": (("mesh", dict(mesh=f"{VVP}/SK_Hatchback", radius=29, width=19, drive="FRONT_WHEEL_DRIVE",
                                       torque=260, rpm=6200, mass=1150)), "Everyday (hatchback)",
                         dict(steer_at_speed=0.38, countersteer=0.5, downforce_coefficient=0.3, yaw_from_steering=0.12)),
    "BP_Car_Sport": (("copy", "/Game/VehicleTemplate/Blueprints/SportsCar/BP_VehicleAdvSportsCar"), "Sport",
                     dict(steer_at_speed=0.30, countersteer=0.45, downforce_coefficient=0.9, yaw_from_steering=0.2)),
    "BP_Car_Pickup": (("mesh", dict(mesh=f"{VVP}/SK_Pickup", radius=32, width=24, drive="REAR_WHEEL_DRIVE",
                                    torque=520, rpm=5200, mass=1900)), "Pickup",
                      dict(steer_at_speed=0.42, countersteer=0.55, downforce_coefficient=0.2, yaw_from_steering=0.08)),
    "BP_Car_Offroad": (("copy", "/Game/VehicleTemplate/Blueprints/OffroadCar/BP_VehicleAdvOffroadCar"), "Off-road (buggy)",
                       dict(steer_at_speed=0.40, countersteer=0.55, downforce_coefficient=0.3, yaw_from_steering=0.1,
                            air_levelling=0.8)),
    "BP_Car_SUV": (("mesh", dict(mesh=f"{VVP}/SK_SUV", radius=38, width=26, drive="ALL_WHEEL_DRIVE",
                                 torque=480, rpm=5600, mass=1800)), "Off-road (SUV)",
                   dict(steer_at_speed=0.40, countersteer=0.55, downforce_coefficient=0.25, yaw_from_steering=0.08)),
}

ENGINE = {"BP_Car_Sport": (750.0, 1500.0), "BP_Car_Offroad": (300.0, 1500.0)}   # torque N.m, mass kg
# Forward gear ratios where the source's are wrong: Epic's off-road template lists 8 forward gears, 5-8 repeating 1-4 (a
# shift into 5th fell back to a 1st-gear ratio).
GEARS = {"BP_Car_Offroad": [3.0, 2.0, 1.4, 1.05, 0.85]}

TRAFFIC = ["/Game/VehicleVarietyPack/Meshes/SM_Hatchback", "/Game/VehicleVarietyPack/Meshes/SM_SUV",
           "/Game/VehicleVarietyPack/Meshes/SM_Pickup", "/Game/VehicleVarietyPack/Meshes/SM_SportsCar",
           "/Game/VehicleVarietyPack/Meshes/SM_Truck_Box", "/Game/Vehicles/SportsCar/SM_SportsCar"]

MOVEMENT = ("wheel_setups", "engine_setup", "transmission_setup", "differential_setup", "steering_setup", "mass",
            "chassis_width", "chassis_height", "drag_coefficient", "downforce_coefficient", "enable_center_of_mass_override",
            "center_of_mass_override", "inertia_tensor_scale")


def restore_from(name, source):
    """A reparented copy can lose what its original set on the inherited mesh and movement (the template sports car did):
    copy them back from the original's defaults when missing."""
    src = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(source))
    dst = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}"))
    sm, dm = src.get_editor_property("mesh"), dst.get_editor_property("mesh")
    sv, dv = src.get_editor_property("vehicle_movement_component"), dst.get_editor_property("vehicle_movement_component")
    fixed = []
    if not dm.get_editor_property("skeletal_mesh_asset"):
        for k in ("skeletal_mesh_asset", "anim_class", "override_materials"):
            dm.set_editor_property(k, sm.get_editor_property(k))
        fixed.append("mesh")
    if len(dv.get_editor_property("wheel_setups")) == 0:
        for k in MOVEMENT:
            try:
                dv.set_editor_property(k, sv.get_editor_property(k))
            except Exception:
                pass
        fixed.append("movement")
    return fixed



def make_blueprint(name, parent):
    path = f"{ROOT}/{name}"
    if eal.does_asset_exist(path):
        return unreal.load_asset(path)
    f = unreal.BlueprintFactory()
    f.set_editor_property("parent_class", parent)
    return assets.create_asset(name, ROOT, unreal.Blueprint, f)


def wheel_class(name, parent, radius, width, engine, mass=0):
    bp = make_blueprint(name, parent)
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    cdo = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}"))
    cdo.set_editor_property("wheel_radius", float(radius))
    cdo.set_editor_property("wheel_width", float(width))
    cdo.set_editor_property("affected_by_engine", engine)
    if mass:
        # springs and damping for the car's mass (the defaults suit the 1500 kg template sports car)
        cdo.set_editor_property("spring_rate", 250.0 * mass / 1500.0)
        cdo.set_editor_property("suspension_damping_ratio", 0.55)
    bp.modify()
    eal.save_loaded_asset(bp)
    return unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}")


def make_car_from_mesh(name, spec):
    for n in (name, f"{name}_WheelF", f"{name}_WheelR"):
        if eal.does_asset_exist(f"{ROOT}/{n}"):
            eal.delete_asset(f"{ROOT}/{n}")
    drive = spec["drive"]
    front = wheel_class(f"{name}_WheelF", unreal.BeratWheelFront, spec["radius"], spec["width"], drive != "REAR_WHEEL_DRIVE", spec["mass"])
    rear = wheel_class(f"{name}_WheelR", unreal.BeratWheelRear, spec["radius"], spec["width"], drive != "FRONT_WHEEL_DRIVE", spec["mass"])
    bp = make_blueprint(name, unreal.BeratCar)
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    cdo = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}"))
    mesh = cdo.get_editor_property("mesh")
    mesh.set_editor_property("skeletal_mesh_asset", unreal.load_asset(spec["mesh"]))
    mesh.set_editor_property("anim_class", unreal.BeratWheelAnimInstance.static_class())
    mv = cdo.get_editor_property("vehicle_movement_component")
    tpl = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(
        "/Game/VehicleTemplate/Blueprints/SportsCar/BP_VehicleAdvSportsCar")).get_editor_property("vehicle_movement_component")
    setups = []
    for bone in WHEELS:
        w = unreal.ChaosWheelSetup()
        w.set_editor_property("wheel_class", front if "Front" in bone else rear)
        w.set_editor_property("bone_name", bone)
        setups.append(w)
    mv.set_editor_property("wheel_setups", setups)
    eng = tpl.get_editor_property("engine_setup")
    eng.set_editor_property("max_torque", float(spec["torque"]))
    eng.set_editor_property("max_rpm", float(spec["rpm"]))
    mv.set_editor_property("engine_setup", eng)
    mv.set_editor_property("transmission_setup", tpl.get_editor_property("transmission_setup"))
    diff = tpl.get_editor_property("differential_setup")
    diff.set_editor_property("differential_type", getattr(unreal.VehicleDifferential, drive))
    mv.set_editor_property("differential_setup", diff)
    mv.set_editor_property("steering_setup", tpl.get_editor_property("steering_setup"))
    mv.set_editor_property("mass", float(spec["mass"]))
    bp.modify()
    eal.save_loaded_asset(bp)
    return bp


def make_car(name, source):
    path = f"{ROOT}/{name}"
    if eal.does_asset_exist(path):
        eal.delete_asset(path)
    bp = eal.duplicate_asset(source, path)
    unreal.BlueprintEditorLibrary.reparent_blueprint(bp, unreal.BeratCar)
    return bp


def run():
    classes = []
    for name, ((how, source), label, preset) in CARS.items():
        if how == "mesh":
            bp = make_car_from_mesh(name, source)
            fixed = []
        else:
            bp = make_car(name, source)
            unreal.BlueprintEditorLibrary.compile_blueprint(bp)
            fixed = restore_from(name, source)
            if name in ENGINE:                    # the template's own figures (a restore can pick up edited values)
                dv = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}")).get_editor_property("vehicle_movement_component")
                eng = dv.get_editor_property("engine_setup")
                eng.set_editor_property("max_torque", ENGINE[name][0])
                dv.set_editor_property("engine_setup", eng)
                dv.set_editor_property("mass", ENGINE[name][1])
            if name in GEARS:
                dv = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}")).get_editor_property("vehicle_movement_component")
                t = dv.get_editor_property("transmission_setup")
                t.set_editor_property("forward_gear_ratios", GEARS[name])
                dv.set_editor_property("transmission_setup", t)
        cdo = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/{name}"))
        mv = cdo.get_editor_property("vehicle_movement_component")
        unreal.log(f"[berat] {name}: mesh {cdo.get_editor_property('mesh').get_editor_property('skeletal_mesh_asset')}, "
                   f"{len(mv.get_editor_property('wheel_setups'))} wheels, restored {fixed}")
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
    for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if a.get_actor_label() == "Berat_Traffic":
            a.modify()
            a.set_editor_property("models", [unreal.load_asset(p) for p in TRAFFIC if unreal.load_asset(p)])
    # the map uses it
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    ws = world.get_world_settings()
    ws.set_editor_property("default_game_mode", unreal.EditorAssetLibrary.load_blueprint_class(f"{ROOT}/BP_BeratGameMode"))
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    return [c.get_name() for c in classes]
