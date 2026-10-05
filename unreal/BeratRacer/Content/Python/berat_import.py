"""berat-racer importer, the editor half (Unreal Editor Python). Reads the map package and the importer cache
(unreal/importer/prep.py, prep_objects.py, fetch_textures.py) and builds the world:

  textures and materials (terrain layers, roads, buildings, openings, water, lamps)
  the World Partition map /Game/Berat/Maps/Berat
  the landscape (C++: BeratImporter.import_landscape), checked against height.tif checkpoints
  roads.glb, buildings.glb, openings.glb, water.glb per sector, materials replaced by name
  the lane graph asset, plants and lamps per sector
  sky, sun, moon, fog, clouds, post process, time of day, lamp lights, traffic, the spawn

Run in the editor (Output Log, Python):  import berat_import, importlib; importlib.reload(berat_import); berat_import.run()
or headless:  UnrealEditor-Cmd.exe BeratRacer.uproject -run=pythonscript -script="berat_import.py"
Every step logs its time; the numbers go to Saved/berat_import.json.
"""

import json
import os
import time

import unreal

PACKAGE = r"C:\Users\jack\berat70scale-1m"
CACHE = r"C:\Users\jack\berat-cache\b3x3"
TEXTURES = r"C:\Users\jack\berat-cache\textures"
ROOT = "/Game/Berat"
MAP = ROOT + "/Maps/Berat"

assets = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary
mel = unreal.MaterialEditingLibrary
log = {}


def step(name):
    """Decorator-free timer: with step("x"): ..."""

    class _T:
        def __enter__(self):
            self.t = time.time()
            unreal.log(f"[berat] {name} ...")

        def __exit__(self, *a):
            dt = time.time() - self.t
            log.setdefault("seconds", {})[name] = round(dt, 1)
            unreal.log(f"[berat] {name}: {dt:.1f} s")

    return _T()


# ---------------------------------------------------------------------------------------------------------------- textures

def import_textures():
    """Every <role>/<role>_{color,normal,arm}.* into /Game/Berat/Textures/<role>; normals and ARM linear."""
    tasks = []
    for role in sorted(os.listdir(TEXTURES)):
        d = os.path.join(TEXTURES, role)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            dest = f"{ROOT}/Textures/{role}"
            if eal.does_asset_exist(f"{dest}/{os.path.splitext(f)[0]}"):
                continue
            t = unreal.AssetImportTask()
            t.filename = os.path.join(d, f)
            t.destination_path = dest
            t.automated = True
            t.save = False
            t.replace_existing = True
            tasks.append(t)
    assets.import_asset_tasks(tasks)
    for t in tasks:
        for p in t.imported_object_paths:
            tex = unreal.load_asset(p)
            name = tex.get_name()
            if name.endswith("_normal"):
                tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
                tex.set_editor_property("srgb", False)
                tex.set_editor_property("flip_green_channel", False)
            elif name.endswith("_arm"):
                tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
                tex.set_editor_property("srgb", False)
            tex.set_editor_property("virtual_texture_streaming", False)
            eal.save_loaded_asset(tex)
    log["textures"] = len(tasks)


def tex(role, kind):
    return unreal.load_asset(f"{ROOT}/Textures/{role}/{role}_{kind}")


# --------------------------------------------------------------------------------------------------------------- materials

class Graph:
    """Small helper over MaterialEditingLibrary: nodes laid out in columns so the graphs stay readable in the editor."""

    def __init__(self, path, **props):
        folder, name = path.rsplit("/", 1)
        if eal.does_asset_exist(path):
            eal.delete_asset(path)
        self.m = assets.create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
        for k, v in props.items():
            self.m.set_editor_property(k, v)
        self.y = {}

    def node(self, cls, col=0, **props):
        y = self.y.get(col, 0)
        self.y[col] = y + 160
        n = mel.create_material_expression(self.m, cls, -300 * (col + 1), y)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def link(self, a, a_out, b, b_in):
        mel.connect_material_expressions(a, a_out, b, b_in)

    def out(self, node, out, prop):
        mel.connect_material_property(node, out, prop)

    def scalar(self, name, value, col=6):
        return self.node(unreal.MaterialExpressionScalarParameter, col, parameter_name=name, default_value=value)

    def const(self, value, col=6):
        return self.node(unreal.MaterialExpressionConstant, col, r=value)

    def texture(self, t, uv=None, col=3, normal=False, linear=False):
        s = self.node(unreal.MaterialExpressionTextureSample, col, texture=t)
        s.set_editor_property("sampler_source", unreal.SamplerSourceMode.SSM_WRAP_WORLD_GROUP_SETTINGS)
        if normal:
            s.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        elif linear:
            s.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        if uv is not None:
            self.link(uv, "", s, "UVs")
        return s

    def save(self):
        mel.layout_material_expressions(self.m)
        mel.recompile_material(self.m)
        eal.save_loaded_asset(self.m)
        return self.m


def world_uv(g, metres, col=5, rotate=0.0):
    """World XY / (metres * 100) as UVs (plan-projected tiling), optionally rotated (radians)."""
    wp = g.node(unreal.MaterialExpressionWorldPosition, col + 1)
    mask = g.node(unreal.MaterialExpressionComponentMask, col, r=True, g=True, b=False, a=False)
    g.link(wp, "", mask, "")
    div = g.node(unreal.MaterialExpressionDivide, col, const_b=metres * 100.0)
    g.link(mask, "", div, "A")
    if rotate:
        rot = g.node(unreal.MaterialExpressionRotator, col - 1, speed=0.0, time=rotate)
        g.link(div, "", rot, "Coordinate")
        return rot
    return div


# Terrain layer -> (surface role, tile metres, tint sRGB multiplier). Roles come from fetch_textures.py.
TERRAIN = {
    "none": ("bare", 3.0, (1.0, 0.97, 0.92)),
    "meadow": ("meadow", 2.5, (0.92, 1.0, 0.85)),
    "cereal": ("cereal", 3.0, (1.12, 1.02, 0.78)),
    "row_crop": ("row_crop", 3.0, (1.0, 0.95, 0.9)),
    "vineyard": ("vineyard", 3.0, (1.0, 0.95, 0.88)),
    "orchard": ("meadow_b", 2.5, (0.95, 1.0, 0.85)),
    "fallow": ("fallow", 2.5, (1.0, 1.0, 0.9)),
    "parking": ("parking", 4.0, (1.0, 1.0, 1.0)),
    "garden": ("garden", 2.5, (0.9, 1.0, 0.85)),
    "yard": ("yard", 3.0, (1.0, 1.0, 1.0)),
    "forest": ("forest", 3.0, (0.95, 0.95, 0.9)),
    "cemetery": ("cemetery", 3.0, (1.0, 1.0, 1.0)),
    "pitch": ("meadow", 2.0, (0.85, 1.05, 0.75)),
    "scrub": ("scrub", 3.0, (1.0, 1.0, 0.95)),
}


def terrain_material(layer_names):
    """Weight-blended landscape layers, one per class present: colour, normal, roughness from each layer's surface, plan
    projected; a second, larger tiling breaks the repetition; steep slopes turn to rock."""
    g = Graph(f"{ROOT}/Materials/M_Terrain")
    blend_c = g.node(unreal.MaterialExpressionLandscapeLayerBlend, 1)
    blend_n = g.node(unreal.MaterialExpressionLandscapeLayerBlend, 1)
    blend_r = g.node(unreal.MaterialExpressionLandscapeLayerBlend, 1)
    layers_c, layers_n, layers_r = [], [], []
    for name in layer_names:
        role, metres, tint = TERRAIN.get(name, ("bare", 3.0, (1, 1, 1)))
        uv = world_uv(g, metres)
        uv2 = world_uv(g, metres * 7.3, rotate=0.7)
        c1 = g.texture(tex(role, "color"), uv)
        c2 = g.texture(tex(role, "color"), uv2)
        lerp = g.node(unreal.MaterialExpressionLinearInterpolate, 2, const_alpha=0.4)
        g.link(c1, "RGB", lerp, "A")
        g.link(c2, "RGB", lerp, "B")
        tint_n = g.node(unreal.MaterialExpressionConstant3Vector, 2, constant=unreal.LinearColor(*tint, 1.0))
        mul = g.node(unreal.MaterialExpressionMultiply, 2)
        g.link(lerp, "", mul, "A")
        g.link(tint_n, "", mul, "B")
        n = g.texture(tex(role, "normal"), uv, normal=True)
        a = g.texture(tex(role, "arm"), uv, linear=True)
        for lst, src, out in ((layers_c, mul, ""), (layers_n, n, "RGB"), (layers_r, a, "G")):
            lst.append((name, src, out))
    for blend, lst in ((blend_c, layers_c), (blend_n, layers_n), (blend_r, layers_r)):
        arr = []
        for name, _, _ in lst:
            li = unreal.LayerBlendInput()
            li.set_editor_property("layer_name", name)
            li.set_editor_property("blend_type", unreal.LandscapeLayerBlendType.LB_WEIGHT_BLEND)
            arr.append(li)
        blend.set_editor_property("layers", arr)
        for name, src, out in lst:
            g.link(src, out, blend, f"Layer {name}")
    # rock on steep ground: slope from the vertex normal
    vn = g.node(unreal.MaterialExpressionVertexNormalWS, 2)
    vz = g.node(unreal.MaterialExpressionComponentMask, 2, r=False, g=False, b=True, a=False)
    g.link(vn, "", vz, "")
    steep = g.node(unreal.MaterialExpressionSmoothStep, 1)
    g.link(vz, "", steep, "Value")
    steep.set_editor_property("const_min", 0.82)
    steep.set_editor_property("const_max", 0.62)
    rock_uv = world_uv(g, 4.0)
    rock_c = g.texture(tex("rock", "color"), rock_uv)
    rock_n = g.texture(tex("rock", "normal"), rock_uv, normal=True)
    lc = g.node(unreal.MaterialExpressionLinearInterpolate, 0)
    g.link(blend_c, "", lc, "A")
    g.link(rock_c, "RGB", lc, "B")
    g.link(steep, "", lc, "Alpha")
    ln = g.node(unreal.MaterialExpressionLinearInterpolate, 0)
    g.link(blend_n, "", ln, "A")
    g.link(rock_n, "RGB", ln, "B")
    g.link(steep, "", ln, "Alpha")
    g.out(lc, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(ln, "", unreal.MaterialProperty.MP_NORMAL)
    g.out(blend_r, "", unreal.MaterialProperty.MP_ROUGHNESS)
    return g.save()


def surface_material(path, role, uv_metres=1.0, tint=(1, 1, 1), roughness_scale=1.0, vertex_colour=False, two_sided=False,
                     uv_index=0, world=False):
    """A tiled PBR surface. UVs from the mesh (metres in the package's glTF) or plan-projected world UVs."""
    g = Graph(path, two_sided=two_sided)
    if world:
        uv = world_uv(g, uv_metres)
    else:
        tc = g.node(unreal.MaterialExpressionTextureCoordinate, 4, coordinate_index=uv_index,
                    u_tiling=1.0 / uv_metres, v_tiling=1.0 / uv_metres)
        uv = tc
    c = g.texture(tex(role, "color"), uv)
    n = g.texture(tex(role, "normal"), uv, normal=True)
    a = g.texture(tex(role, "arm"), uv, linear=True)
    tint_n = g.node(unreal.MaterialExpressionConstant3Vector, 2, constant=unreal.LinearColor(*tint, 1.0))
    mul = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(c, "RGB", mul, "A")
    g.link(tint_n, "", mul, "B")
    base = mul
    if vertex_colour:
        vc = g.node(unreal.MaterialExpressionVertexColor, 2)
        # the data's colour sets the hue, the texture the detail: colour * texture luminance * 2
        desat = g.node(unreal.MaterialExpressionDesaturation, 1)
        g.link(mul, "", desat, "")
        m2 = g.node(unreal.MaterialExpressionMultiply, 0)
        g.link(desat, "", m2, "A")
        g.link(vc, "", m2, "B")
        m3 = g.node(unreal.MaterialExpressionMultiply, 0, const_b=2.0)
        g.link(m2, "", m3, "A")
        lerp = g.node(unreal.MaterialExpressionLinearInterpolate, 0, const_alpha=0.65)
        g.link(mul, "", lerp, "A")
        g.link(m3, "", lerp, "B")
        base = lerp
    rs = g.node(unreal.MaterialExpressionMultiply, 1, const_b=roughness_scale)
    g.link(a, "G", rs, "A")
    g.out(base, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(n, "RGB", unreal.MaterialProperty.MP_NORMAL)
    g.out(rs, "", unreal.MaterialProperty.MP_ROUGHNESS)
    g.out(a, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
    return g.save()


def paint_material():
    g = Graph(f"{ROOT}/Materials/M_Paint")
    c = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(0.8, 0.8, 0.78, 1))
    r = g.const(0.55)
    g.out(c, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(r, "", unreal.MaterialProperty.MP_ROUGHNESS)
    return g.save()


def night_param(g, col=4):
    mpc = unreal.load_asset(f"{ROOT}/Materials/MPC_Berat")
    p = g.node(unreal.MaterialExpressionCollectionParameter, col, collection=mpc, parameter_name="Night")
    return p


def opening_material(path, glass_colour, frame_colour, lit_share, emissive_colour, opaque_glass=False):
    """Windows and doors from UVs: a frame border, glass with a sky-ish reflection; at night a share of them (by the vertex
    colour's random R) glow warm."""
    g = Graph(path)
    tc = g.node(unreal.MaterialExpressionTextureCoordinate, 5)
    # frame mask: 1 near the border (8 % of the opening), plus a central mullion
    cu = g.node(unreal.MaterialExpressionComponentMask, 4, r=True, g=False, b=False, a=False)
    cv = g.node(unreal.MaterialExpressionComponentMask, 4, r=False, g=True, b=False, a=False)
    g.link(tc, "", cu, "")
    g.link(tc, "", cv, "")
    def edge(x):
        # min(x, 1 - x) < 0.08 -> frame
        one_minus = g.node(unreal.MaterialExpressionOneMinus, 3)
        g.link(x, "", one_minus, "")
        mn = g.node(unreal.MaterialExpressionMin, 3)
        g.link(x, "", mn, "A")
        g.link(one_minus, "", mn, "B")
        st = g.node(unreal.MaterialExpressionStep, 2, const_y=0.08)
        g.link(mn, "", st, "X")
        return st, mn
    eu, mnu = edge(cu)
    ev, _ = edge(cv)
    mull_d = g.node(unreal.MaterialExpressionSubtract, 3, const_b=0.5)
    g.link(cu, "", mull_d, "A")
    mull_a = g.node(unreal.MaterialExpressionAbs, 3)
    g.link(mull_d, "", mull_a, "")
    mull = g.node(unreal.MaterialExpressionStep, 2, const_y=0.03)
    g.link(mull_a, "", mull, "X")
    mx = g.node(unreal.MaterialExpressionMax, 2)
    g.link(eu, "", mx, "A")
    g.link(ev, "", mx, "B")
    frame = g.node(unreal.MaterialExpressionMax, 1)
    g.link(mx, "", frame, "A")
    g.link(mull, "", frame, "B")
    gc = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(*glass_colour, 1))
    fc = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(*frame_colour, 1))
    base = g.node(unreal.MaterialExpressionLinearInterpolate, 0)
    g.link(gc, "", base, "A")
    g.link(fc, "", base, "B")
    g.link(frame, "", base, "Alpha")
    rough = g.node(unreal.MaterialExpressionLinearInterpolate, 0, const_a=0.05 if not opaque_glass else 0.6, const_b=0.6)
    g.link(frame, "", rough, "Alpha")
    spec = g.node(unreal.MaterialExpressionLinearInterpolate, 0, const_a=1.0, const_b=0.4)
    g.link(frame, "", spec, "Alpha")
    # night glow: step(R, lit_share) * Night * (1 - frame) * colour
    vc = g.node(unreal.MaterialExpressionVertexColor, 3)
    lit = g.node(unreal.MaterialExpressionStep, 2, const_y=lit_share)
    g.link(vc, "R", lit, "X")
    one_minus_f = g.node(unreal.MaterialExpressionOneMinus, 1)
    g.link(frame, "", one_minus_f, "")
    night = night_param(g)
    e1 = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(lit, "", e1, "A")
    g.link(night, "", e1, "B")
    e2 = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(e1, "", e2, "A")
    g.link(one_minus_f, "", e2, "B")
    ec = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(*emissive_colour, 1))
    e3 = g.node(unreal.MaterialExpressionMultiply, 0)
    g.link(e2, "", e3, "A")
    g.link(ec, "", e3, "B")
    g.out(base, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    g.out(spec, "", unreal.MaterialProperty.MP_SPECULAR)
    g.out(e3, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return g.save()


def shutter_material():
    """Wooden shutters: horizontal slats from UV v, the vertex colour as paint."""
    g = Graph(f"{ROOT}/Materials/M_Shutter")
    tc = g.node(unreal.MaterialExpressionTextureCoordinate, 4, v_tiling=14.0)
    v = g.node(unreal.MaterialExpressionComponentMask, 3, r=False, g=True, b=False, a=False)
    g.link(tc, "", v, "")
    fr = g.node(unreal.MaterialExpressionFrac, 2)
    g.link(v, "", fr, "")
    slat = g.node(unreal.MaterialExpressionSmoothStep, 1, const_min=0.0, const_max=0.25)
    g.link(fr, "", slat, "Value")
    shade = g.node(unreal.MaterialExpressionLinearInterpolate, 1, const_a=0.55, const_b=1.0)
    g.link(slat, "", shade, "Alpha")
    vc = g.node(unreal.MaterialExpressionVertexColor, 2)
    mul = g.node(unreal.MaterialExpressionMultiply, 0)
    g.link(vc, "", mul, "A")
    g.link(shade, "", mul, "B")
    g.out(mul, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(g.const(0.7), "", unreal.MaterialProperty.MP_ROUGHNESS)
    return g.save()


def water_material():
    """Translucent-free water: dark base, very smooth, normals from two panning tiled normals (uses the meadow normal map as
    a ripple source is wrong; the engine's own water normal is used when present)."""
    g = Graph(f"{ROOT}/Materials/M_Water")
    c = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(0.02, 0.045, 0.04, 1))
    g.out(c, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(g.const(0.03), "", unreal.MaterialProperty.MP_ROUGHNESS)
    g.out(g.const(0.5), "", unreal.MaterialProperty.MP_SPECULAR)
    ripple = unreal.load_asset("/Engine/EngineMaterials/T_Default_Material_Grid_N") or None
    wn = unreal.load_asset("/Engine/Functions/Engine_MaterialFunctions02/ExampleContent/Textures/water_n")
    if wn:
        uv1 = world_uv(g, 6.0)
        pan = g.node(unreal.MaterialExpressionPanner, 3, speed_x=0.01, speed_y=0.006)
        g.link(uv1, "", pan, "Coordinate")
        n = g.texture(wn, pan, normal=True)
        flat = g.node(unreal.MaterialExpressionFlattenNormal, 1, )
        g.link(n, "RGB", flat, "Normal")
        g.out(flat, "", unreal.MaterialProperty.MP_NORMAL)
    return g.save()


def lamp_head_material():
    g = Graph(f"{ROOT}/Materials/M_LampHead")
    night = night_param(g)
    c = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(40.0, 28.0, 16.0, 1))
    e = g.node(unreal.MaterialExpressionMultiply, 0)
    g.link(night, "", e, "A")
    g.link(c, "", e, "B")
    g.out(g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(0.7, 0.7, 0.7, 1)), "",
          unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(e, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return g.save()


def make_materials(layer_names):
    if not eal.does_asset_exist(f"{ROOT}/Materials/MPC_Berat"):
        mpc = assets.create_asset("MPC_Berat", f"{ROOT}/Materials", unreal.MaterialParameterCollection,
                                  unreal.MaterialParameterCollectionFactoryNew())
        p = unreal.CollectionScalarParameter()
        p.set_editor_property("parameter_name", "Night")
        p.set_editor_property("default_value", 0.0)
        mpc.set_editor_property("scalar_parameters", [p])
        eal.save_loaded_asset(mpc)
    m = {}
    m["terrain"] = terrain_material(layer_names)
    # roads: carriageway UV0 = (m across, m along); junctions and car parks plan metres
    m["asphalt"] = surface_material(f"{ROOT}/Materials/M_Asphalt", "asphalt", 4.0, (0.9, 0.9, 0.9))
    m["dirt"] = surface_material(f"{ROOT}/Materials/M_DirtRoad", "dirt_road", 3.0)
    m["deck"] = surface_material(f"{ROOT}/Materials/M_Deck", "concrete", 3.0, two_sided=True)
    m["paint"] = paint_material()
    # buildings: walls UV0 (m along, m up), vertex colour = the data's wall colour; roofs plan metres, roof colour
    m["wall"] = surface_material(f"{ROOT}/Materials/M_Wall", "wall_plaster", 2.5, vertex_colour=True)
    m["roof"] = surface_material(f"{ROOT}/Materials/M_Roof", "roof_canal", 2.0, vertex_colour=True)
    m["window"] = opening_material(f"{ROOT}/Materials/M_Window", (0.05, 0.06, 0.07), (0.85, 0.84, 0.8), 0.35, (8.0, 5.5, 3.0))
    m["door"] = opening_material(f"{ROOT}/Materials/M_Door", (0.28, 0.18, 0.11), (0.3, 0.2, 0.12), 0.0, (0, 0, 0), True)
    m["garage"] = opening_material(f"{ROOT}/Materials/M_Garage", (0.6, 0.6, 0.58), (0.5, 0.5, 0.48), 0.0, (0, 0, 0), True)
    m["shopfront"] = opening_material(f"{ROOT}/Materials/M_Shopfront", (0.04, 0.05, 0.06), (0.2, 0.2, 0.22), 0.9, (10.0, 9.0, 7.0))
    m["shutter"] = shutter_material()
    m["water"] = water_material()
    m["lamp_head"] = lamp_head_material()
    return m


# ------------------------------------------------------------------------------------------------------------------ world

def new_map():
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if eal.does_asset_exist(MAP):
        les.load_level(MAP)
        return
    les.new_level(MAP, True)          # World Partition


def import_landscape(material):
    secs = unreal.BeratImporter.import_landscape(CACHE, material, f"{ROOT}/Landscape", 2, )
    land, seconds = secs if isinstance(secs, tuple) else (secs, -1)
    log["landscape_seconds"] = round(seconds, 1)
    return land


def check_heights():
    block = json.load(open(os.path.join(CACHE, "block.json")))
    errs = []
    for c in block["checkpoints"]:
        z = unreal.BeratImporter.trace_height(None, c["x"], c["y"])
        errs.append({"x": c["x"], "y": c["y"], "z_tif": c["z_tif"], "z_unreal": round(z, 4), "err_cm": round((z - c["z_tif"]) * 100, 2)})
    worst = max(abs(e["err_cm"]) for e in errs)
    log["height_check"] = {"points": len(errs), "max_err_cm": worst, "points_detail": errs}
    unreal.log(f"[berat] heights: {len(errs)} checkpoints, max |unreal - height.tif| = {worst:.2f} cm")
    return worst


def import_glb(path, dest, name):
    """glTF into a static mesh asset, node transform kept out of the vertices (the actor carries the sector's corner)."""
    t = unreal.AssetImportTask()
    t.filename = path
    t.destination_path = dest
    t.destination_name = name
    t.automated = True
    t.save = True
    t.replace_existing = True
    assets.import_asset_tasks([t])
    meshes = [unreal.load_asset(p) for p in t.imported_object_paths]
    return [m for m in meshes if isinstance(m, unreal.StaticMesh)]


def setup_mesh(mesh, materials, collision, nanite=True):
    for i, slot in enumerate(mesh.static_materials):
        name = str(slot.material_slot_name).lower()
        for key, mat in materials.items():
            if name.startswith(key):
                mesh.set_material(i, mat)
                break
    ns = mesh.get_editor_property("nanite_settings")
    ns.set_editor_property("enabled", nanite)
    mesh.set_editor_property("nanite_settings", ns)
    body = mesh.get_editor_property("body_setup")
    if body:
        body.set_editor_property("collision_trace_flag",
                                 unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE if collision else unreal.CollisionTraceFlag.CTF_USE_DEFAULT)
    eal.save_loaded_asset(mesh)


def place(mesh, label, si, sj, collision):
    """Actor at the sector's south-west corner (the glTF node's translation), in Unreal axes."""
    x, y = 3200 * si - 16000, 3200 * sj - 16000
    actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_object(
        mesh, unreal.Vector(x * 100.0, -y * 100.0, 0.0))
    actor.set_actor_label(label)
    comp = actor.static_mesh_component
    comp.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS if collision else unreal.CollisionEnabled.NO_COLLISION)
    return actor


def import_sector_meshes(materials):
    block = json.load(open(os.path.join(CACHE, "block.json")))
    n = {"roads": 0, "buildings": 0, "openings": 0, "water": 0}
    for si, sj in block["sectors"]:
        key = f"{si}_{sj}"
        src = os.path.join(PACKAGE, "sectors", key)
        cache = os.path.join(CACHE, "sectors", key)
        for kind, folder, collision in (("roads", src, True), ("buildings", src, True), ("openings", cache, False), ("water", cache, False)):
            path = os.path.join(folder, f"{kind}.glb")
            if not os.path.exists(path):
                continue
            for mesh in import_glb(path, f"{ROOT}/Sectors/{key}", f"SM_{kind}_{key}"):
                setup_mesh(mesh, materials, collision)
                actor = place(mesh, f"{kind}_{key}", si, sj, collision)
                n[kind] += 1
    log["meshes"] = n


def spawn(cls, label, location=(0, 0, 0), rotation=(0, 0, 0)):
    a = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
        cls, unreal.Vector(*location), unreal.Rotator(*rotation))
    a.set_actor_label(label)
    return a


def setup_sky_and_game(lane_graph, traffic_models):
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for a in eas.get_all_level_actors():
        if a.get_actor_label().startswith("Berat_"):
            eas.destroy_actor(a)
    sun = spawn(unreal.DirectionalLight, "Berat_Sun")
    sl = sun.light_component
    sl.set_editor_property("atmosphere_sun_light", True)
    sl.set_editor_property("atmosphere_sun_light_index", 0)
    sl.set_editor_property("intensity", 100000.0)   # lux
    sl.set_editor_property("light_source_angle", 0.53)
    sun.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    moon = spawn(unreal.DirectionalLight, "Berat_Moon")
    ml = moon.light_component
    ml.set_editor_property("atmosphere_sun_light", True)
    ml.set_editor_property("atmosphere_sun_light_index", 1)
    ml.set_editor_property("intensity", 0.5)
    ml.set_editor_property("light_color", unreal.Color(200, 215, 255, 255))
    ml.set_editor_property("cast_shadows", True)
    moon.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    spawn(unreal.SkyAtmosphere, "Berat_Atmosphere")
    sky = spawn(unreal.SkyLight, "Berat_SkyLight")
    sky.light_component.set_editor_property("real_time_capture", True)
    sky.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    clouds = spawn(unreal.VolumetricCloud, "Berat_Clouds")
    fog = spawn(unreal.ExponentialHeightFog, "Berat_Fog", (0, 0, 20000))
    fc = fog.component
    fc.set_editor_property("fog_density", 0.004)
    fc.set_editor_property("fog_height_falloff", 0.08)
    fc.set_editor_property("volumetric_fog", True)
    pp = spawn(unreal.PostProcessVolume, "Berat_PostProcess")
    pp.set_editor_property("unbound", True)
    s = pp.settings
    s.set_editor_property("override_auto_exposure_method", True)
    s.set_editor_property("auto_exposure_method", unreal.AutoExposureMethod.AEM_HISTOGRAM)
    s.set_editor_property("override_auto_exposure_min_brightness", True)
    s.set_editor_property("auto_exposure_min_brightness", -4.0)
    s.set_editor_property("override_auto_exposure_max_brightness", True)
    s.set_editor_property("auto_exposure_max_brightness", 16.0)
    s.set_editor_property("override_auto_exposure_bias", True)
    s.set_editor_property("auto_exposure_bias", 1.0)
    pp.set_editor_property("settings", s)
    tod = spawn(unreal.BeratTimeOfDay, "Berat_TimeOfDay")
    tod.set_editor_property("sun", sun)
    tod.set_editor_property("moon", moon)
    tod.set_editor_property("sky", sky)
    tod.set_editor_property("fog", fog)
    tod.set_editor_property("parameters", unreal.load_asset(f"{ROOT}/Materials/MPC_Berat"))
    spawn(unreal.BeratLampLights, "Berat_LampLights")
    tr = spawn(unreal.BeratTraffic, "Berat_Traffic")
    tr.set_editor_property("graph", lane_graph)
    tr.set_editor_property("models", traffic_models)
    # spawn point: the manifest's, on the road
    man = json.load(open(os.path.join(PACKAGE, "manifest.json")))
    sp = man["spawn"]
    ps = spawn(unreal.PlayerStart, "Berat_Spawn", (sp["x"] * 100, -sp["y"] * 100, sp["z"] * 100 + 100),
               (0, 0, sp["heading"] - 90.0))
    # these actors stay loaded whatever the streaming
    for a in (sun, moon, sky, clouds, fog, pp, tod, tr, ps):
        try:
            a.set_editor_property("is_spatially_loaded", False)
        except Exception:
            pass


def lamp_mesh():
    """A plain lamp post: a 8 m pole, a 1.6 m arm over the road (+X), a head; engine basic shapes merged would need the
    modeling tools, so the post is the engine cylinder and the head is handled by the light pool (emissive head later)."""
    return unreal.load_asset("/Engine/BasicShapes/Cylinder")


def run(skip_textures=False):
    t0 = time.time()
    block = json.load(open(os.path.join(CACHE, "block.json")))
    layer_names = [l["name"] for l in block["layers"]]
    if not skip_textures:
        with step("textures"):
            import_textures()
    with step("materials"):
        materials = make_materials(layer_names)
    with step("map"):
        new_map()
    with step("landscape"):
        import_landscape(materials["terrain"])
    with step("height check"):
        check_heights()
    with step("sector meshes"):
        import_sector_meshes(materials)
    with step("lane graph"):
        graph = unreal.BeratImporter.import_lane_graph(os.path.join(CACHE, "lanes.json"), f"{ROOT}/Data/LaneGraph")
    with step("sky and game"):
        setup_sky_and_game(graph, [])
    with step("save"):
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_all_dirty_levels()
        eal.save_directory(ROOT, only_if_is_dirty=True, recursive=True)
    log["seconds"]["total"] = round(time.time() - t0, 1)
    out = os.path.join(unreal.Paths.project_saved_dir(), "berat_import.json")
    with open(out, "w") as f:
        json.dump(log, f, indent=1)
    unreal.log(f"[berat] done in {time.time() - t0:.0f} s, log in {out}")
    return log
