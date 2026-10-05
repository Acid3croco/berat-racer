"""Screenshots of the editor world without the viewport (works with the editor in the background): a SceneCapture2D renders
into a render target, exported as PNG to Saved/Shots/<name>.png.

    import berat_shots; berat_shots.shot("spawn", (x, y, z), (pitch, yaw))     # package metres, degrees (Unreal yaw)
"""

import math
import os

import unreal

OUT = os.path.join(unreal.Paths.project_saved_dir(), "Shots")


def _world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def shot(name, at, look, width=1920, height=1080, fov=75.0, exposure_bias=None):
    """at: package metres (x east, y north, z NGF); look: (pitch, yaw) in Unreal degrees."""
    os.makedirs(OUT, exist_ok=True)
    world = _world()
    rt = unreal.RenderingLibrary.create_render_target2d(world, width, height, unreal.TextureRenderTargetFormat.RTF_RGBA8_SRGB)
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    loc = unreal.Vector(at[0] * 100.0, -at[1] * 100.0, at[2] * 100.0)
    cap = eas.spawn_actor_from_class(unreal.SceneCapture2D, loc, unreal.Rotator(roll=0.0, pitch=look[0], yaw=look[1]))
    c = cap.capture_component2d
    c.set_editor_property("texture_target", rt)
    c.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_TONE_CURVE_HDR)
    c.set_editor_property("fov_angle", fov)
    c.set_editor_property("capture_every_frame", False)
    c.set_editor_property("always_persist_rendering_state", True)
    if exposure_bias is not None:
        pp = c.get_editor_property("post_process_settings")
        pp.set_editor_property("override_auto_exposure_bias", True)
        pp.set_editor_property("auto_exposure_bias", exposure_bias)
        c.set_editor_property("post_process_settings", pp)
    # a few captures so eye adaptation, Lumen and virtual shadow maps settle
    for _ in range(6):
        c.capture_scene()
    unreal.RenderingLibrary.export_render_target(world, rt, OUT, f"{name}.png")
    eas.destroy_actor(cap)
    return os.path.join(OUT, f"{name}.png")


def spawn_views():
    """The views used to check each import: behind the spawn, and an aerial over the village."""
    x, y, z, h = 54.09, -35.5, 245.75, 32.8
    east, north = math.sin(math.radians(h)), math.cos(math.radians(h))    # heading: clockwise from north
    yaw = h - 90.0                                                         # Unreal yaw of that heading
    return [
        shot("spawn", (x - east * 25, y - north * 25, z + 6), (-10.0, yaw)),
        shot("aerial", (x - 600, y - 600, z + 400), (-32.0, -45.0)),      # from the south-west, looking north-east
    ]
