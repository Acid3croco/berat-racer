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
            # reuse the asset (deleting one still referenced, e.g. by a landscape, fails and the re-create then waits on an
            # "Overwrite Existing Object" dialog)
            self.m = unreal.load_asset(path)
            mel.delete_all_material_expressions(self.m)
        else:
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
        # a non-integer offset per tiling scale: the two scales never line up, the repetition breaks
        off = g.node(unreal.MaterialExpressionConstant2Vector, col, r=rotate * 0.37, g=rotate * 0.61)
        add = g.node(unreal.MaterialExpressionAdd, col - 1)
        g.link(div, "", add, "A")
        g.link(off, "", add, "B")
        return add
    return div


# Terrain layer -> (surface role, tile metres, tint sRGB multiplier). Roles come from fetch_textures.py.
TERRAIN = {
    "bare": ("bare", 3.0, (1.0, 0.97, 0.92)),
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
    rows_c = row_furrows(g, blend_c, [n for n in ("cereal", "row_crop") if n in layer_names])
    g.link(rows_c, "", lc, "A")
    g.link(rock_c, "RGB", lc, "B")
    g.link(steep, "", lc, "Alpha")
    ln = g.node(unreal.MaterialExpressionLinearInterpolate, 0)
    g.link(blend_n, "", ln, "A")
    g.link(rock_n, "RGB", ln, "B")
    g.link(steep, "", ln, "Alpha")
    base = macro_colour(g, lc)
    add_grass_output(g, layer_names)
    g.out(base, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(ln, "", unreal.MaterialProperty.MP_NORMAL)
    g.out(blend_r, "", unreal.MaterialProperty.MP_ROUGHNESS)
    return g.save()


def import_rows():
    """The block's crop-row directions (prep_rows.py) as a linear texture, not virtual."""
    info = json.load(open(os.path.join(CACHE, "rows_dir.json")))
    path = f"{ROOT}/Textures/T_RowsDir"
    t = unreal.AssetImportTask()
    t.filename = os.path.join(CACHE, "rows_dir.png")
    t.destination_path = f"{ROOT}/Textures"
    t.destination_name = "T_RowsDir"
    t.automated = True
    t.save = True
    t.replace_existing = True
    assets.import_asset_tasks([t])
    tx = unreal.load_asset(path)
    tx.set_editor_property("srgb", False)
    tx.set_editor_property("virtual_texture_streaming", False)
    tx.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
    eal.save_loaded_asset(tx)
    return tx, info


ROWS_DEBUG = False      # strong furrows on every rowed cell (layer weights ignored): checks the chain


def row_furrows(g, detail, row_layers=("cereal", "row_crop")):
    """Furrows along each field's real row direction (rows texture): stripes 0.75 m apart darken the detail colour by up
    to 18 %, weighted by the rowed layers and the texture's mask, faded out beyond ~50 m."""
    tx, info = import_rows()
    nx, ny = info["pixels"]
    cell_cm = info["cell"] * 100.0
    wp = g.node(unreal.MaterialExpressionWorldPosition, 9)
    xy = g.node(unreal.MaterialExpressionComponentMask, 8, r=True, g=True, b=False, a=False)
    g.link(wp, "", xy, "")
    scale = g.node(unreal.MaterialExpressionConstant2Vector, 8, r=1.0 / cell_cm / nx, g=1.0 / cell_cm / ny)
    off = g.node(unreal.MaterialExpressionConstant2Vector, 8, r=(-info["x0"] / info["cell"]) / nx, g=(info["ytop"] / info["cell"]) / ny)
    mul = g.node(unreal.MaterialExpressionMultiply, 7)
    g.link(xy, "", mul, "A")
    g.link(scale, "", mul, "B")
    uv = g.node(unreal.MaterialExpressionAdd, 7)
    g.link(mul, "", uv, "A")
    g.link(off, "", uv, "B")
    s = g.node(unreal.MaterialExpressionTextureSample, 6, texture=tx)
    s.set_editor_property("sampler_source", unreal.SamplerSourceMode.SSM_CLAMP_WORLD_GROUP_SETTINGS)
    s.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
    g.link(uv, "", s, "UVs")
    # doubled angle back to the row direction: theta = atan2(2G - 1, 2R - 1) / 2
    def unpack(ch):
        m = g.node(unreal.MaterialExpressionMultiply, 5, const_b=2.0)
        g.link(s, ch, m, "A")
        sub = g.node(unreal.MaterialExpressionSubtract, 5, const_b=1.0)
        g.link(m, "", sub, "A")
        return sub
    c2, s2 = unpack("R"), unpack("G")
    at = g.node(unreal.MaterialExpressionArctangent2, 5)
    g.link(s2, "", at, "Y")
    g.link(c2, "", at, "X")
    th = g.node(unreal.MaterialExpressionMultiply, 5, const_b=0.5)
    g.link(at, "", th, "A")
    sn = g.node(unreal.MaterialExpressionSine, 4, period=6.283185307)
    cs = g.node(unreal.MaterialExpressionCosine, 4, period=6.283185307)
    g.link(th, "", sn, "")
    g.link(th, "", cs, "")
    # across the rows (package axes: direction (cos, sin); Unreal Y = -north): s = (X sin + Y cos) / 100 m
    wx = g.node(unreal.MaterialExpressionComponentMask, 5, r=True, g=False, b=False, a=False)
    wy = g.node(unreal.MaterialExpressionComponentMask, 5, r=False, g=True, b=False, a=False)
    g.link(wp, "", wx, "")
    g.link(wp, "", wy, "")
    a1 = g.node(unreal.MaterialExpressionMultiply, 4)
    g.link(wx, "", a1, "A")
    g.link(sn, "", a1, "B")
    a2 = g.node(unreal.MaterialExpressionMultiply, 4)
    g.link(wy, "", a2, "A")
    g.link(cs, "", a2, "B")
    acr = g.node(unreal.MaterialExpressionAdd, 4)
    g.link(a1, "", acr, "A")
    g.link(a2, "", acr, "B")
    per = g.node(unreal.MaterialExpressionDivide, 3, const_b=75.0)        # 0.75 m between rows
    g.link(acr, "", per, "A")
    fr = g.node(unreal.MaterialExpressionFrac, 3)
    g.link(per, "", fr, "")
    half = g.node(unreal.MaterialExpressionSubtract, 3, const_b=0.5)
    g.link(fr, "", half, "A")
    ab = g.node(unreal.MaterialExpressionAbs, 3)
    g.link(half, "", ab, "")
    tri = g.node(unreal.MaterialExpressionMultiply, 3, const_b=2.0)      # 0 in the furrow, 1 on the ridge
    g.link(ab, "", tri, "A")
    shade = g.node(unreal.MaterialExpressionLinearInterpolate, 2, const_a=0.3 if ROWS_DEBUG else 0.66, const_b=1.06)
    g.link(tri, "", shade, "Alpha")
    # weight x near (fade 30-60 m)
    wm = g.node(unreal.MaterialExpressionMultiply, 2)
    # weight: the rows mask alone (set only on rowed parcels: crops, vines, orchards); the landscape layer-weight nodes
    # read 0 in this material
    g.link(g.const(1.0), "", wm, "A")
    g.link(s, "B", wm, "B")
    depth = g.node(unreal.MaterialExpressionPixelDepth, 3)
    near = g.node(unreal.MaterialExpressionSmoothStep, 2, const_min=6000.0, const_max=3000.0)
    g.link(depth, "", near, "Value")
    wn = g.node(unreal.MaterialExpressionMultiply, 2)
    g.link(wm, "", wn, "A")
    g.link(near, "", wn, "B")
    sat = g.node(unreal.MaterialExpressionSaturate, 2)
    g.link(wn, "", sat, "")
    fac = g.node(unreal.MaterialExpressionLinearInterpolate, 1, const_a=1.0)
    g.link(shade, "", fac, "B")
    g.link(sat, "", fac, "Alpha")
    outm = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(detail, "", outm, "A")
    g.link(fac, "", outm, "B")
    return outm


def import_colour():
    """The block's real ground colour (prep_colour.py) as a texture; returns it and its placement."""
    info = json.load(open(os.path.join(CACHE, "colour.json")))
    path = f"{ROOT}/Textures/Macro/T_GroundColour"
    if not eal.does_asset_exist(path):
        t = unreal.AssetImportTask()
        t.filename = os.path.join(CACHE, "colour.png")
        t.destination_path = f"{ROOT}/Textures/Macro"
        t.destination_name = "T_GroundColour"
        t.automated = True
        t.replace_existing = True
        assets.import_asset_tasks([t])
    tex_ = unreal.load_asset(path)
    tex_.set_editor_property("power_of_two_mode", unreal.TexturePowerOfTwoSetting.STRETCH_TO_POWER_OF_TWO)
    tex_.set_editor_property("address_x", unreal.TextureAddress.TA_CLAMP)
    tex_.set_editor_property("address_y", unreal.TextureAddress.TA_CLAMP)
    tex_.set_editor_property("lod_group", unreal.TextureGroup.TEXTUREGROUP_TERRAIN_WEIGHTMAP)
    eal.save_loaded_asset(tex_)
    return tex_, info


def macro_colour(g, detail):
    """Real ground colour over the tiled detail: near, the detail keeps its texture and takes the real colour (colour x
    detail luminance); far (50 to 300 m), the real colour alone (no tiling seen from afar)."""
    tex_, info = import_colour()
    nx, ny = info["pixels"]
    # UV of a world point: pixel centres on the 4 m grid from (x0, ytop), x east, y north (Unreal Y = -north)
    wp = g.node(unreal.MaterialExpressionWorldPosition, 7)
    xy = g.node(unreal.MaterialExpressionComponentMask, 6, r=True, g=True, b=False, a=False)
    g.link(wp, "", xy, "")
    scale = g.node(unreal.MaterialExpressionConstant2Vector, 6, r=0.0025 / nx, g=0.0025 / ny)
    off = g.node(unreal.MaterialExpressionConstant2Vector, 6, r=(0.5 - info["x0"] / 4.0) / nx, g=(0.5 + info["ytop"] / 4.0) / ny)
    mul = g.node(unreal.MaterialExpressionMultiply, 5)
    g.link(xy, "", mul, "A")
    g.link(scale, "", mul, "B")
    uv = g.node(unreal.MaterialExpressionAdd, 5)
    g.link(mul, "", uv, "A")
    g.link(off, "", uv, "B")
    m = g.node(unreal.MaterialExpressionTextureSample, 4, texture=tex_)
    m.set_editor_property("sampler_source", unreal.SamplerSourceMode.SSM_CLAMP_WORLD_GROUP_SETTINGS)
    g.link(uv, "", m, "UVs")
    # near: real colour x detail luminance (x2.2 keeps the overall brightness of the real colour)
    lum = g.node(unreal.MaterialExpressionDesaturation, 3)
    g.link(detail, "", lum, "")
    lm = g.node(unreal.MaterialExpressionMultiply, 3, const_b=2.2)
    g.link(lum, "", lm, "A")
    near = g.node(unreal.MaterialExpressionMultiply, 2)
    g.link(m, "RGB", near, "A")
    g.link(lm, "", near, "B")
    near_mix = g.node(unreal.MaterialExpressionLinearInterpolate, 2, const_alpha=0.75)
    g.link(detail, "", near_mix, "A")
    g.link(near, "", near_mix, "B")
    depth = g.node(unreal.MaterialExpressionPixelDepth, 3)
    far = g.node(unreal.MaterialExpressionSmoothStep, 2, const_min=5000.0, const_max=30000.0)
    g.link(depth, "", far, "Value")
    out = g.node(unreal.MaterialExpressionLinearInterpolate, 1)
    g.link(near_mix, "", out, "A")
    g.link(m, "RGB", out, "B")
    g.link(far, "", out, "Alpha")
    return out


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
    """Opaque stream / pond water: a murky green-brown body, a little rough, and ripples from two panning scales of the
    engine's water normal (the sky's reflection broken up; a near-mirror read as flat cyan ribbons)."""
    g = Graph(f"{ROOT}/Materials/M_Water")
    c = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(0.035, 0.045, 0.028, 1))
    g.out(c, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(g.const(0.12), "", unreal.MaterialProperty.MP_ROUGHNESS)
    g.out(g.const(0.35), "", unreal.MaterialProperty.MP_SPECULAR)
    wn = unreal.load_asset("/Engine/Functions/Engine_MaterialFunctions02/ExampleContent/Textures/water_n")
    if wn:
        uv1 = world_uv(g, 3.0)
        pan1 = g.node(unreal.MaterialExpressionPanner, 3, speed_x=0.03, speed_y=0.012)
        g.link(uv1, "", pan1, "Coordinate")
        n1 = g.texture(wn, pan1, normal=True)
        uv2 = world_uv(g, 0.9, rotate=1.3)
        pan2 = g.node(unreal.MaterialExpressionPanner, 3, speed_x=-0.05, speed_y=0.04)
        g.link(uv2, "", pan2, "Coordinate")
        n2 = g.texture(wn, pan2, normal=True)
        add = g.node(unreal.MaterialExpressionAdd, 2)
        g.link(n1, "RGB", add, "A")
        g.link(n2, "RGB", add, "B")
        norm = g.node(unreal.MaterialExpressionNormalize, 2)
        g.link(add, "", norm, "")
        up = g.node(unreal.MaterialExpressionConstant3Vector, 2, constant=unreal.LinearColor(0, 0, 1, 1))
        mix = g.node(unreal.MaterialExpressionLinearInterpolate, 1, const_alpha=0.75)
        g.link(up, "", mix, "A")
        g.link(norm, "", mix, "B")
        g.out(mix, "", unreal.MaterialProperty.MP_NORMAL)
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
    # full detail further out (road edges stand 5 cm over the terrain; coarse far LODs cut through them). Not Nanite
    # landscape: its build took 1151 s and 75 GB for the 3 x 3 block, with cracks between proxies.
    land.set_editor_property("lod0_screen_size", 1.0)
    land.set_editor_property("lod0_distribution_setting", 4.0)
    land.set_editor_property("lod_distribution_setting", 4.0)
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
    """Materials by slot name; Nanite with a full-detail fallback (collision is built from the fallback: an automatic,
    simplified one put the road's collision 5 cm under the drawn road, or lost it); complex-as-simple collision."""
    for i, slot in enumerate(mesh.static_materials):
        name = str(slot.material_slot_name).lower()
        for key, mat in materials.items():
            if name.startswith(key):
                mesh.set_material(i, mat)
                break
    body = mesh.get_editor_property("body_setup")
    if body:
        body.set_editor_property("collision_trace_flag",
                                 unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE if collision else unreal.CollisionTraceFlag.CTF_USE_DEFAULT)
    ns = mesh.get_editor_property("nanite_settings")
    ns.enabled = nanite
    ns.fallback_target = unreal.NaniteFallbackTarget.PERCENT_TRIANGLES
    ns.fallback_percent_triangles = 1.0
    unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).set_nanite_settings(mesh, ns, apply_changes=True)
    eal.save_loaded_asset(mesh)


def place(mesh, label, si, sj, collision):
    """Actor at the world origin: Unreal's glTF import (Interchange) bakes the node's translation (the sector's south-west
    corner) into the vertices and converts glTF axes to Unreal's (UE.Y = -north), so the mesh is already in world space."""
    actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_object(mesh, unreal.Vector(0.0, 0.0, 0.0))
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
        # roads: drawn without collision (their skirts made walls at every edge); roads_collision: the drivable surfaces only
        # (prep_objects.py), invisible. Roads, openings, water without Nanite (thin strips and quads Nanite simplifies badly).
        for kind, folder, collision in (("roads", src, False), ("roads_collision", cache, True), ("buildings", src, True),
                                        ("openings", cache, False), ("water", cache, False)):
            path = os.path.join(folder, f"{kind}.glb")
            if not os.path.exists(path):
                continue
            for mesh in import_glb(path, f"{ROOT}/Sectors/{key}", f"SM_{kind}_{key}"):
                setup_mesh(mesh, materials, collision, nanite=kind == "buildings")
                actor = place(mesh, f"{kind}_{key}", si, sj, collision)
                if kind == "roads_collision":
                    actor.set_actor_hidden_in_game(True)
                    actor.static_mesh_component.set_visibility(False)
                n[kind] = n.get(kind, 0) + 1
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
    fc.set_editor_property("enable_volumetric_fog", True)
    pp = spawn(unreal.PostProcessVolume, "Berat_PostProcess")
    pp.set_editor_property("unbound", True)
    s = pp.settings
    s.set_editor_property("override_auto_exposure_method", True)
    s.set_editor_property("auto_exposure_method", unreal.AutoExposureMethod.AEM_HISTOGRAM)
    s.set_editor_property("override_auto_exposure_min_brightness", True)
    s.set_editor_property("auto_exposure_min_brightness", 2.0)   # EV100: night stays night (street lights read as lights)
    s.set_editor_property("override_auto_exposure_max_brightness", True)
    s.set_editor_property("auto_exposure_max_brightness", 15.0)
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


def pin_all():
    """Load (and keep loaded) every World Partition actor of the map: a fresh editor session starts with them unloaded."""
    wp = unreal.WorldPartitionBlueprintLibrary
    wp.pin_actors([d.guid for d in wp.get_actor_descs()])


def save():
    """Every dirty package, World Partition's external actor packages included (save_all_dirty_levels skips them)."""
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)


def finish():
    """The steps after the landscape and the meshes: lane graph (reloaded), sky, game actors, save."""
    t0 = time.time()
    graph = unreal.load_asset(f"{ROOT}/Data/LaneGraph")
    with step("sky and game"):
        setup_sky_and_game(graph, [])
    with step("save"):
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_all_dirty_levels()
        eal.save_directory(ROOT, only_if_is_dirty=True, recursive=True)
    unreal.log(f"[berat] finish done in {time.time() - t0:.0f} s")


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
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    pin_all()
    if any(isinstance(a, unreal.Landscape) for a in eas.get_all_level_actors()):
        unreal.log("[berat] landscape already in the map: kept")
    else:
        with step("landscape"):
            import_landscape(materials["terrain"])
        with step("save landscape"):
            save()
    with step("height check"):
        check_heights()
    if not any(a.get_actor_label().startswith("roads_") for a in eas.get_all_level_actors()):
        with step("sector meshes"):
            import_sector_meshes(materials)
        with step("save meshes"):
            save()
    with step("lane graph"):
        graph = unreal.BeratImporter.import_lane_graph(os.path.join(CACHE, "lanes.json"), f"{ROOT}/Data/LaneGraph")
    with step("sky and game"):
        setup_sky_and_game(graph, [])
    with step("save"):
        save()
    log["seconds"]["total"] = round(time.time() - t0, 1)
    out = os.path.join(unreal.Paths.project_saved_dir(), "berat_import.json")
    with open(out, "w") as f:
        json.dump(log, f, indent=1)
    unreal.log(f"[berat] done in {time.time() - t0:.0f} s, log in {out}")
    return log


def foliage_material():
    """Masked, two-sided foliage (Nanite draws masked, not translucent): colour and opacity from one texture parameter."""
    path = f"{ROOT}/Materials/M_Foliage"
    g = Graph(path, blend_mode=unreal.BlendMode.BLEND_MASKED, two_sided=True,
              shading_model=unreal.MaterialShadingModel.MSM_TWO_SIDED_FOLIAGE)
    t = g.node(unreal.MaterialExpressionTextureSampleParameter2D, 2, parameter_name="BaseColorTexture",
               texture=unreal.load_asset("/Engine/EngineResources/DefaultTexture"))
    tint = g.node(unreal.MaterialExpressionVectorParameter, 2, parameter_name="Tint",
                  default_value=unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    mul = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(t, "RGB", mul, "A")
    g.link(tint, "", mul, "B")
    sss = g.node(unreal.MaterialExpressionMultiply, 1, const_b=0.5)
    g.link(mul, "", sss, "A")
    g.out(mul, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(t, "A", unreal.MaterialProperty.MP_OPACITY_MASK)
    g.out(sss, "", unreal.MaterialProperty.MP_SUBSURFACE_COLOR)
    g.out(g.const(0.75), "", unreal.MaterialProperty.MP_ROUGHNESS)
    m = g.save()
    m.set_editor_property("opacity_mask_clip_value", 0.4)
    eal.save_loaded_asset(m)
    return m


def fix_model_foliage(model_root=f"{ROOT}/Models"):
    """Every imported model material that glTF made translucent or masked becomes an instance of M_Foliage with the same
    colour texture, so the leaves draw under Nanite."""
    parent = foliage_material()
    fixed = 0
    for p in eal.list_assets(model_root, recursive=True):
        mic = unreal.load_asset(p)
        if not isinstance(mic, unreal.MaterialInstanceConstant):
            continue
        par = mic.get_editor_property("parent")
        if not par or not any(k in par.get_name() for k in ("Blend", "Mask", "Translucent")):
            continue
        tex_ = None
        for tp in mic.get_editor_property("texture_parameter_values"):
            name = str(tp.get_editor_property("parameter_info").get_editor_property("name")).lower()
            if "basecolor" in name or "diffuse" in name or "albedo" in name:
                tex_ = tp.get_editor_property("parameter_value")
        mic.set_editor_property("parent", parent)
        if tex_:
            unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(mic, "BaseColorTexture", tex_)
        unreal.MaterialEditingLibrary.update_material_instance(mic)
        eal.save_loaded_asset(mic)
        fixed += 1
    return fixed


GRASS_LAYERS = {"meadow": 1.0, "fallow": 0.8, "garden": 0.9, "orchard": 0.9, "pitch": 0.6, "scrub": 0.6, "cemetery": 0.3}


def grass_type():
    """Native landscape grass from the Project Nature grass library (Fab): short meadow tufts, medium clumps, a few tall
    ones, at their own size, scattered by the GPU near the camera on the grassy classes."""
    path = f"{ROOT}/Landscape/LGT_Meadow"
    gt = unreal.load_asset(path) if eal.does_asset_exist(path) else assets.create_asset(
        "LGT_Meadow", f"{ROOT}/Landscape", unreal.LandscapeGrassType, unreal.LandscapeGrassTypeFactory())
    # mesh -> instances per 10 x 10 m
    pick = {"grass_01_01_mesh": 300, "grass_01_04_mesh": 300, "grass_01_07_mesh": 250, "grass_02_01_mesh": 120,
            "grass_02_04_mesh": 120}
    meshes = {}
    for p in eal.list_assets("/Game/PN_GrassLibrary/FoliageTypes", recursive=True):
        m = unreal.load_asset(p).get_editor_property("mesh")
        if m and m.get_name() in pick:
            meshes[m.get_name()] = m
    varieties = []
    for name, density in pick.items():
        m = meshes.get(name)
        if not m:
            continue
        v = unreal.GrassVariety()
        v.set_editor_property("grass_mesh", m)
        v.set_editor_property("grass_density", unreal.PerPlatformFloat(default=float(density)))
        v.set_editor_property("start_cull_distance", unreal.PerPlatformInt(default=2500))
        v.set_editor_property("end_cull_distance", unreal.PerPlatformInt(default=4500))
        v.set_editor_property("random_rotation", True)
        v.set_editor_property("align_to_surface", True)
        # landscape grass inherits the landscape's Z scale (137.7 cm per step here, not 100): undo it on Z
        zs = 100.0 / 137.6974
        v.set_editor_property("scaling", unreal.GrassScaling.FREE)
        v.set_editor_property("scale_x", unreal.FloatInterval(0.9, 1.2))
        v.set_editor_property("scale_y", unreal.FloatInterval(0.9, 1.2))
        v.set_editor_property("scale_z", unreal.FloatInterval(0.85 * zs, 1.15 * zs))
        v.set_editor_property("cast_dynamic_shadow", False)
        varieties.append(v)
    gt.set_editor_property("grass_varieties", varieties)
    eal.save_loaded_asset(gt)
    return gt, len(varieties)


def add_grass_output(g, layer_names):
    """LandscapeGrassOutput fed by the sum of the grassy layers' weights."""
    gt, _ = grass_type()
    total = None
    for name, k in GRASS_LAYERS.items():
        if name not in layer_names:
            continue
        s = g.node(unreal.MaterialExpressionLandscapeLayerSample, 3, parameter_name=name)
        if k != 1.0:
            m = g.node(unreal.MaterialExpressionMultiply, 2, const_b=k)
            g.link(s, "", m, "A")
            s = m
        if total is None:
            total = s
        else:
            a = g.node(unreal.MaterialExpressionAdd, 2)
            g.link(total, "", a, "A")
            g.link(s, "", a, "B")
            total = a
    if total is None:
        return
    out = g.node(unreal.MaterialExpressionLandscapeGrassOutput, 1)
    gi = unreal.GrassInput()
    gi.set_editor_property("name", "Meadow")
    gi.set_editor_property("grass_type", gt)
    out.set_editor_property("grass_types", [gi])
    g.link(total, "", out, "Meadow")


WORLD = r"C:\Users\jack\berat-cache\world"


def free_ram_gb():
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**30


def import_world(regions=None, min_free_gb=24.0):
    """The whole package's terrain: one landscape per region of WORLD (prep_world.py), each imported, checked against
    height.tif, saved, then the map reloaded so the editor's memory stays bounded. Skips regions already in the map."""
    t0 = time.time()
    man = json.load(open(os.path.join(PACKAGE, "manifest.json")))
    all_layers = ["bare" if c == "none" else c.replace(" ", "_") for c in man["classes"]]
    material = terrain_material(all_layers)
    names = regions or sorted(d for d in os.listdir(WORLD) if os.path.isfile(os.path.join(WORLD, d, "block.json")))
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    report = log.setdefault("world", {})
    for name in names:
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        if any(a.get_actor_label() == f"Landscape_{name}" for a in eas.get_all_level_actors()):
            continue
        if free_ram_gb() < min_free_gb:
            unreal.log_warning(f"[berat] {free_ram_gb():.1f} GB free: stopping before {name}")
            break
        d = os.path.join(WORLD, name)
        t = time.time()
        res = unreal.BeratImporter.import_landscape(d, material, f"{ROOT}/Landscape", 2)
        land = res[0] if isinstance(res, tuple) else res
        land.set_editor_property("lod0_screen_size", 1.0)
        land.set_editor_property("lod0_distribution_setting", 4.0)
        land.set_editor_property("lod_distribution_setting", 4.0)
        block = json.load(open(os.path.join(d, "block.json")))
        errs = [abs(unreal.BeratImporter.trace_height(None, c["x"], c["y"]) - c["z_tif"]) * 100 for c in block["checkpoints"]]
        save()
        les.load_level(MAP)                       # drop the region from memory
        report[name] = {"seconds": round(time.time() - t, 1), "check_max_cm": round(max(errs), 2) if errs else None,
                        "free_gb": round(free_ram_gb(), 1)}
        unreal.log(f"[berat] region {name}: {report[name]}")
    report["total_seconds"] = round(time.time() - t0, 1)
    return report


def remove_block_landscape():
    """Delete the 3 x 3 test landscape (and its streaming proxies) before the world's regions are imported."""
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    gone = [a for a in eas.get_all_level_actors()
            if (isinstance(a, (unreal.Landscape, unreal.LandscapeStreamingProxy))
                and not (a.get_actor_label().startswith("Landscape_r") or "Landscape_r" in a.get_path_name()))]
    for a in gone:
        eas.destroy_actor(a)
    save()
    return len(gone)


def import_world_objects(sectors=None, batch=20, min_free_gb=24.0):
    """Roads (drawn and collision), buildings, openings, water and props of every package sector not yet in the map;
    WORLD_OBJECTS is prep_objects.py --all. Saves and reloads the map every `batch` sectors."""
    t0 = time.time()
    man = json.load(open(os.path.join(PACKAGE, "manifest.json")))
    cache_root = r"C:\Users\jack\berat-cache\world_objects"
    materials = make_materials(["bare" if c == "none" else c.replace(" ", "_") for c in man["classes"]])
    kinds, lamp = prop_meshes()
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    done = 0
    for si, sj in sectors or sorted(tuple(s) for s in man["sectors"]):
        key = f"{si}_{sj}"
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        labels = {a.get_actor_label() for a in eas.get_all_level_actors()}
        if f"roads_{key}" in labels and f"Props_{key}" in labels:
            continue
        if free_ram_gb() < min_free_gb:
            unreal.log_warning(f"[berat] {free_ram_gb():.1f} GB free: stopping before {key}")
            break
        src = os.path.join(PACKAGE, "sectors", key)
        cache = os.path.join(cache_root, "sectors", key)
        for kind, folder, collision in (("roads", src, False), ("roads_collision", cache, True), ("buildings", src, True),
                                        ("openings", cache, False), ("water", cache, False)):
            path = os.path.join(folder, f"{kind}.glb")
            if f"{kind}_{key}" in labels or not os.path.exists(path):
                continue
            for mesh in import_glb(path, f"{ROOT}/Sectors/{key}", f"SM_{kind}_{key}"):
                setup_mesh(mesh, materials, collision, nanite=kind == "buildings")
                actor = place(mesh, f"{kind}_{key}", si, sj, collision)
                if kind == "roads_collision":
                    actor.set_actor_hidden_in_game(True)
                    actor.static_mesh_component.set_visibility(False)
                if not collision:
                    actor.static_mesh_component.set_collision_profile_name("NoCollision")
        if f"Props_{key}" not in labels:
            unreal.BeratImporter.import_sector_props(None, cache, si, sj, kinds, lamp, unreal.Vector(0, 0, 360), 250000.0)
        done += 1
        if done % batch == 0:
            save()
            les.load_level(MAP)
            unreal.log(f"[berat] world objects: {done} sectors, {time.time() - t0:.0f} s, {free_ram_gb():.1f} GB free")
    save()
    return {"sectors": done, "seconds": round(time.time() - t0, 1)}


MEGA = "/Game/Megaplant_Library"
# plant kind (prep_objects.KINDS) -> Megaplants species (every A..D variant of each)
SPECIES = {0: ("English_Oak", "European_Beech", "Hornbeam", "Black_Alder", "Silver_Birch"), 1: ("English_Oak", "European_Beech", "Hornbeam"),
           2: ("Norway_Spruce",), 3: ("Black_Poplar",), 4: ("Goat_Willow",), 5: ("Common_Hazel", "Elder"),
           6: ("Common_Hazel", "Elder", "Goat_Willow"), 7: (), 8: (), 9: ()}
# kinds drawn with a street prop instead of a plant
PROP_KIND_MESH = {8: "/Game/Mega_Street_Props_Pack/Street_Props_Pack_V1/Mesh/SM_Trash",
                  9: "/Game/Mega_Street_Props_Pack/Street_Props_Pack_V1/Mesh/SM_Bench"}


def species_meshes(name, baked=False):
    """The species' Megaplants trees: the skinned Nanite originals (Nanite Foliage on: assemblies of leafy twigs, wind), or
    the static bakes (BeratImporter.bake_skeletal_to_static)."""
    out = []
    if baked:
        for p in eal.list_assets(f"{ROOT}/Trees", recursive=False):
            o = unreal.load_asset(p)
            if isinstance(o, unreal.StaticMesh) and o.get_name().startswith(f"SM_Tree_{name}_"):
                out.append(o)
        return out
    for p in eal.list_assets(f"{MEGA}/Tree_{name}", recursive=True):
        if "/Instances/" in p:
            continue
        o = unreal.load_asset(p)
        if isinstance(o, unreal.SkeletalMesh):
            out.append(o)
    return out


def prop_meshes(baked=False):
    """Plant kind -> FBeratKindMeshes (Megaplants skinned trees; Poly Haven where Fab has nothing), lamp post."""
    def mesh(d, name):
        for p in eal.list_assets(f"{ROOT}/Models/{d}", recursive=True):
            if p.split(".")[-1] == name:
                o = unreal.load_asset(p)
                if isinstance(o, unreal.StaticMesh):
                    return o
        raise RuntimeError(f"{d}/{name}")
    cache = {}
    kinds = []
    for k in range(len(SPECIES)):
        km = unreal.BeratKindMeshes()
        skinned = []
        for sp in SPECIES[k]:
            if sp not in cache:
                cache[sp] = species_meshes(sp, baked)
            skinned += cache[sp]
        km.set_editor_property("static" if baked else "skinned", skinned)
        km.set_editor_property("cull_distance", {5: 45000.0, 6: 45000.0, 7: 30000.0}.get(k, 0.0))   # shrubs, hedges, vines
        if k in PROP_KIND_MESH:
            km.set_editor_property("static", [unreal.load_asset(PROP_KIND_MESH[k])])
            km.set_editor_property("cull_distance", 6000.0)
        elif not skinned:
            km.set_editor_property("static", [mesh("shrub_b", "shrub_03_a")] if k == 7 else [mesh("broadleaf_small", "tree_small_02")])
        kinds.append(km)
    return kinds, mesh("lamp_a", "street_lamp_01")


# finish -> (surface role, metres per tile, colour from the data (vertex colour) weight, plinth and grime)
FINISHES = {
    "wall_render": ("wall_plaster_c", 1.6, 1.0, True), "wall_brick": ("wall_brick", 1.2, 0.1, True),
    "wall_stone": ("wall_stone", 2.0, 0.15, True), "wall_concrete": ("wall_concrete", 2.0, 0.3, True),
    "wall_metal": ("wall_metal", 2.0, 0.35, False), "wall_wood": ("wall_wood", 2.0, 0.2, True),
    "roof_canal": ("roof_canal", 2.0, 0.55, False), "roof_canal_b": ("roof_canal_b", 2.0, 0.55, False),
    "roof_canal_c": ("roof_canal_c", 2.0, 0.55, False), "roof_slate": ("roof_slate", 2.0, 0.2, False),
    "roof_metal": ("roof_metal", 2.0, 0.3, False), "roof_fibre": ("roof_fibre", 2.5, 0.25, False),
    "roof_flat": ("roof_flat", 3.0, 0.3, False),
}

# finishes whose texture colour pattern is too loud: share of its luminance contrast kept
FLAT_PATTERN = {"wall_render": 0.35}
# tint per finish: Toulouse brick ("brique foraine") is red-orange, the texture reads dark brown
FINISH_TINT = {"wall_brick": (1.3, 0.97, 0.8)}


def building_material(name, role, metres, colour_weight, weathering):
    """A building finish: tiled PBR surface (UV0 in metres), tinted by the data's colour (vertex colour: wall palette or the
    orthophoto's roof colour), varied per building (UV1.x), with a plinth and grime near the ground on walls (UV0.y is
    metres up from the wall's base)."""
    g = Graph(f"{ROOT}/Materials/Buildings/M_{name}", two_sided=name.startswith("roof"))   # roofs: overhang seen from below
    tc = g.node(unreal.MaterialExpressionTextureCoordinate, 6, coordinate_index=0, u_tiling=1.0 / metres, v_tiling=1.0 / metres)
    c = g.texture(tex(role, "color"), tc)
    n = g.texture(tex(role, "normal"), tc, normal=True)
    a = g.texture(tex(role, "arm"), tc, linear=True)
    # data colour: colour = lerp(texture, texture luminance x vertex colour x 2, weight)
    vc = g.node(unreal.MaterialExpressionVertexColor, 4)
    lum = g.node(unreal.MaterialExpressionDesaturation, 3)
    g.link(c, "RGB", lum, "")
    if name in FLAT_PATTERN:
        # keep the grain (normal, roughness) but not the texture's colour pattern: luminance pulled toward 0.5
        flat = g.node(unreal.MaterialExpressionLinearInterpolate, 3, const_a=0.5, const_alpha=FLAT_PATTERN[name])
        g.link(lum, "", flat, "B")
        lum = flat
    tinted = g.node(unreal.MaterialExpressionMultiply, 3)
    g.link(lum, "", tinted, "A")
    g.link(vc, "", tinted, "B")
    t2 = g.node(unreal.MaterialExpressionMultiply, 2, const_b=2.0)
    g.link(tinted, "", t2, "A")
    mix = g.node(unreal.MaterialExpressionLinearInterpolate, 2, const_alpha=colour_weight)
    g.link(c, "RGB", mix, "A")
    g.link(t2, "", mix, "B")
    # per-building shade: x (0.85 .. 1.12) from UV1.x
    tc1 = g.node(unreal.MaterialExpressionTextureCoordinate, 4, coordinate_index=1)
    r = g.node(unreal.MaterialExpressionComponentMask, 3, r=True, g=False, b=False, a=False)
    g.link(tc1, "", r, "")
    shade = g.node(unreal.MaterialExpressionLinearInterpolate, 2, const_a=0.85, const_b=1.12)
    g.link(r, "", shade, "Alpha")
    base = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(mix, "", base, "A")
    g.link(shade, "", base, "B")
    if name in FINISH_TINT:
        tn = g.node(unreal.MaterialExpressionConstant3Vector, 2, constant=unreal.LinearColor(*FINISH_TINT[name], 1.0))
        tb = g.node(unreal.MaterialExpressionMultiply, 1)
        g.link(base, "", tb, "A")
        g.link(tn, "", tb, "B")
        base = tb
    # weathering at large scale (world space, texture-based noise): patches 0.84..1.06 over a few metres, and on walls
    # vertical rain streaks 0.86..1 (noise stretched along Z)
    wp = g.node(unreal.MaterialExpressionWorldPosition, 4)
    pd = g.node(unreal.MaterialExpressionDivide, 3, const_b=350.0)
    g.link(wp, "", pd, "A")
    patch = g.node(unreal.MaterialExpressionNoise, 2, scale=1.0, levels=3, output_min=0.78, output_max=1.08,
                   noise_function=unreal.NoiseFunction.NOISEFUNCTION_GRADIENT_TEX, quality=1)
    g.link(pd, "", patch, "Position")
    pm = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(base, "", pm, "A")
    g.link(patch, "", pm, "B")
    base = pm
    if name.startswith("wall"):
        sv = g.node(unreal.MaterialExpressionConstant3Vector, 4, constant=unreal.LinearColor(1.0 / 45.0, 1.0 / 45.0, 1.0 / 700.0, 1))
        sp = g.node(unreal.MaterialExpressionMultiply, 3)
        g.link(wp, "", sp, "A")
        g.link(sv, "", sp, "B")
        streak = g.node(unreal.MaterialExpressionNoise, 2, scale=1.0, levels=2, output_min=0.74, output_max=1.0,
                        noise_function=unreal.NoiseFunction.NOISEFUNCTION_GRADIENT_TEX, quality=1)
        g.link(sp, "", streak, "Position")
        sm = g.node(unreal.MaterialExpressionMultiply, 1)
        g.link(base, "", sm, "A")
        g.link(streak, "", sm, "B")
        base = sm
    if weathering:
        # metres up the wall (UV0.y), unscaled
        tc0 = g.node(unreal.MaterialExpressionTextureCoordinate, 4, coordinate_index=0)
        up = g.node(unreal.MaterialExpressionComponentMask, 3, r=False, g=True, b=False, a=False)
        g.link(tc0, "", up, "")
        # grime: 0.72 at the foot, 1 from 1.6 m up
        grime = g.node(unreal.MaterialExpressionSmoothStep, 2, const_min=0.0, const_max=1.6)
        g.link(up, "", grime, "Value")
        gl = g.node(unreal.MaterialExpressionLinearInterpolate, 1, const_a=0.72, const_b=1.0)
        g.link(grime, "", gl, "Alpha")
        g1 = g.node(unreal.MaterialExpressionMultiply, 1)
        g.link(base, "", g1, "A")
        g.link(gl, "", g1, "B")
        # plinth: below 0.5 m a grey stone band
        plinth = g.node(unreal.MaterialExpressionStep, 1, const_x=0.5)
        g.link(up, "", plinth, "Y")                     # Step returns 1 when X >= Y: 1 when up <= 0.5 m
        pc = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(0.32, 0.31, 0.29, 1))
        out = g.node(unreal.MaterialExpressionLinearInterpolate, 0)
        g.link(g1, "", out, "A")
        g.link(pc, "", out, "B")
        g.link(plinth, "", out, "Alpha")
        base = out
    g.out(base, "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(n, "RGB", unreal.MaterialProperty.MP_NORMAL)
    g.out(a, "G", unreal.MaterialProperty.MP_ROUGHNESS)
    g.out(a, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
    if name.startswith("roof_metal") or name.startswith("wall_metal"):
        g.out(a, "B", unreal.MaterialProperty.MP_METALLIC)
    return g.save()


def building_materials():
    return {name: building_material(name, *spec) for name, spec in FINISHES.items()}


def restyle_buildings(sectors=None):
    """Replace each sector's buildings mesh by buildings_styled.glb (prep_buildings.py) with the finish materials."""
    import_textures()
    mats = building_materials()
    mats["gutter"] = simple_material(f"{ROOT}/Materials/M_Gutter", vertex_colour=True, roughness=0.4, specular=0.5,
                                     metallic=0.5)
    mats["belfry"] = simple_material(f"{ROOT}/Materials/M_Belfry", colour=(0.02, 0.02, 0.025), roughness=0.9)
    block = json.load(open(os.path.join(CACHE, "block.json")))
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    done = 0
    for si, sj in sectors or block["sectors"]:
        key = f"{si}_{sj}"
        path = os.path.join(CACHE, "sectors", key, "buildings_styled.glb")
        for a in eas.get_all_level_actors():
            if a.get_actor_label() == f"buildings_{key}":
                eas.destroy_actor(a)
        for mesh in import_glb(path, f"{ROOT}/Sectors/{key}", f"SM_buildings_styled_{key}"):
            for i, slot in enumerate(mesh.static_materials):
                m = mats.get(str(slot.material_slot_name))
                if m:
                    mesh.set_material(i, m)
            setup_mesh(mesh, {}, True, nanite=True)
            place(mesh, f"buildings_{key}", si, sj, True)
            done += 1
    save()
    return done


def simple_material(path, colour=None, vertex_colour=False, roughness=0.5, specular=0.5, metallic=0.0, role=None, metres=1.0,
                    lit_share=None, lit_colour=(8.0, 5.5, 3.0)):
    """Small opening materials: constant or vertex colour (optionally over a tiled texture), and for glass a night glow on
    a share of the openings (vertex colour R < lit_share) driven by the Night parameter."""
    g = Graph(path)
    if role:
        tc = g.node(unreal.MaterialExpressionTextureCoordinate, 5, u_tiling=1.0 / metres, v_tiling=1.0 / metres)
        t = g.texture(tex(role, "color"), tc)
        base = t
        if vertex_colour:
            vc = g.node(unreal.MaterialExpressionVertexColor, 4)
            lum = g.node(unreal.MaterialExpressionDesaturation, 3)
            g.link(t, "RGB", lum, "")
            m = g.node(unreal.MaterialExpressionMultiply, 2)
            g.link(lum, "", m, "A")
            g.link(vc, "", m, "B")
            m2 = g.node(unreal.MaterialExpressionMultiply, 1, const_b=2.0)
            g.link(m, "", m2, "A")
            base = m2
        g.out(base, "RGB" if base is t else "", unreal.MaterialProperty.MP_BASE_COLOR)
    elif vertex_colour:
        g.out(g.node(unreal.MaterialExpressionVertexColor, 2), "", unreal.MaterialProperty.MP_BASE_COLOR)
    else:
        g.out(g.node(unreal.MaterialExpressionConstant3Vector, 2, constant=unreal.LinearColor(*colour, 1)), "",
              unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(g.const(roughness), "", unreal.MaterialProperty.MP_ROUGHNESS)
    g.out(g.const(specular), "", unreal.MaterialProperty.MP_SPECULAR)
    if metallic:
        g.out(g.const(metallic), "", unreal.MaterialProperty.MP_METALLIC)
    if lit_share is not None:
        vc = g.node(unreal.MaterialExpressionVertexColor, 3)
        lit = g.node(unreal.MaterialExpressionStep, 2, const_x=lit_share)
        g.link(vc, "R", lit, "Y")                   # 1 when R <= lit_share
        night = night_param(g)
        e = g.node(unreal.MaterialExpressionMultiply, 1)
        g.link(lit, "", e, "A")
        g.link(night, "", e, "B")
        ec = g.node(unreal.MaterialExpressionConstant3Vector, 1, constant=unreal.LinearColor(*lit_colour, 1))
        e2 = g.node(unreal.MaterialExpressionMultiply, 0)
        g.link(e, "", e2, "A")
        g.link(ec, "", e2, "B")
        g.out(e2, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return g.save()


def opening_materials():
    R = f"{ROOT}/Materials/Openings"
    return {
        "glass": simple_material(f"{R}/M_Glass", (0.015, 0.02, 0.025), roughness=0.04, specular=1.0, lit_share=0.35),
        "shopfront": simple_material(f"{R}/M_Shopfront", (0.02, 0.025, 0.03), roughness=0.05, specular=1.0, lit_share=0.9,
                                     lit_colour=(10.0, 9.0, 7.0)),
        "frame": simple_material(f"{R}/M_Frame", vertex_colour=True, roughness=0.45),
        "sill": simple_material(f"{R}/M_Sill", vertex_colour=True, role="wall_stone", metres=1.0, roughness=0.8),
        "door": simple_material(f"{R}/M_DoorLeaf", vertex_colour=True, role="wall_wood", metres=1.5, roughness=0.6),
        "garage": simple_material(f"{R}/M_GarageLeaf", (0.55, 0.55, 0.53), role="wall_metal", metres=1.0, vertex_colour=True,
                                  roughness=0.5),
        "shutter": shutter_material(),
        "surround": simple_material(f"{R}/M_Surround", vertex_colour=True, roughness=0.85, specular=0.3),
    }


def restyle_openings(sectors=None):
    """Replace each sector's openings by the framed ones (prep_objects.py), Nanite on."""
    mats = opening_materials()
    block = json.load(open(os.path.join(CACHE, "block.json")))
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for si, sj in sectors or block["sectors"]:
        key = f"{si}_{sj}"
        for a in eas.get_all_level_actors():
            if a.get_actor_label() == f"openings_{key}":
                eas.destroy_actor(a)
        for mesh in import_glb(os.path.join(CACHE, "sectors", key, "openings.glb"), f"{ROOT}/Sectors/{key}", f"SM_openings_{key}"):
            for i, slot in enumerate(mesh.static_materials):
                m = mats.get(str(slot.material_slot_name))
                if m:
                    mesh.set_material(i, m)
            setup_mesh(mesh, {}, False, nanite=True)
            act = place(mesh, f"openings_{key}", si, sj, False)
            act.static_mesh_component.set_collision_profile_name("NoCollision")
    save()


# --------------------------------------------------------------------------------------------------------------- surfaces

# Grip per surface, the native Chaos way: each wheel reads the Friction of the physical material under it
# (ChaosWheeledVehicleMovementComponent ApplyWheelFrictionForces). The cars were tuned on the default material (0.7).
# Surface types (names in DefaultEngine.ini PhysicsSettings) are what wheel dust and sounds key on.
SURFACES = {           # name: (surface type index, friction)
    "Asphalt": (1, 0.8),
    "Concrete": (2, 0.75),
    "Gravel": (3, 0.6),
    "Dirt": (4, 0.55),
    "Grass": (5, 0.5),
    "Field": (6, 0.45),
    "Mud": (7, 0.38),
    "Forest": (8, 0.5),
    "Rock": (9, 0.7),
}
LAYER_SURFACE = {"bare": "Dirt", "meadow": "Grass", "cereal": "Field", "row_crop": "Mud", "vineyard": "Dirt",
                 "orchard": "Grass", "fallow": "Grass", "garden": "Grass", "yard": "Gravel", "forest": "Forest",
                 "cemetery": "Gravel", "pitch": "Grass", "scrub": "Dirt", "rock": "Rock"}
ROAD_SURFACE = {"M_Asphalt": "Asphalt", "M_DirtRoad": "Gravel", "M_Deck": "Concrete"}


def physical_materials():
    out = {}
    for name, (index, friction) in SURFACES.items():
        path = f"{ROOT}/Physics/PM_{name}"
        pm = unreal.load_asset(path) if eal.does_asset_exist(path) else assets.create_asset(
            f"PM_{name}", f"{ROOT}/Physics", unreal.PhysicalMaterial, unreal.PhysicalMaterialFactoryNew())
        pm.set_editor_property("friction", friction)
        pm.set_editor_property("surface_type", getattr(unreal.PhysicalSurface, f"SURFACE_TYPE{index}"))
        eal.save_loaded_asset(pm)
        out[name] = pm
    return out


def apply_surfaces():
    """Physical materials on the landscape layers (the collision keeps each cell's dominant layer) and on the road
    materials (the road collision mesh has one slot per road material)."""
    pms = physical_materials()
    n = 0
    for layer, surface in LAYER_SURFACE.items():
        path = f"{ROOT}/Landscape/LI_{layer}"
        if eal.does_asset_exist(path):
            li = unreal.load_asset(path)
            li.set_editor_property("phys_material", pms[surface])
            eal.save_loaded_asset(li)
            n += 1
    for mat, surface in ROAD_SURFACE.items():
        m = unreal.load_asset(f"{ROOT}/Materials/{mat}")
        m.set_editor_property("phys_material", pms[surface])
        eal.save_loaded_asset(m)
    log["surfaces"] = {"layers": n, "roads": len(ROAD_SURFACE)}
    return n


# ----------------------------------------------------------------------------------------------------------- garden walls

def railing_material():
    """Railing cut out of one strip (UV0 = metres along, 0..1 up): bars every 12 cm, posts every 2 m, top and bottom rails;
    masked, two-sided, painted (vertex colour)."""
    g = Graph(f"{ROOT}/Materials/M_Railing", blend_mode=unreal.BlendMode.BLEND_MASKED, two_sided=True)
    tc = g.node(unreal.MaterialExpressionTextureCoordinate, 6)
    u = g.node(unreal.MaterialExpressionComponentMask, 5, r=True, g=False, b=False, a=False)
    v = g.node(unreal.MaterialExpressionComponentMask, 5, r=False, g=True, b=False, a=False)
    g.link(tc, "", u, "")
    g.link(tc, "", v, "")

    def below(x, edge, col=3):                       # 1 where x <= edge
        s_ = g.node(unreal.MaterialExpressionStep, col, const_x=edge)
        g.link(x, "", s_, "Y")
        return s_

    def frac_of(scale):
        m = g.node(unreal.MaterialExpressionMultiply, 4, const_b=scale)
        g.link(u, "", m, "A")
        f = g.node(unreal.MaterialExpressionFrac, 4)
        g.link(m, "", f, "")
        return f

    bars = below(frac_of(1.0 / 0.12), 0.17)
    posts = below(frac_of(0.5), 0.03)
    bottom = below(v, 0.05)
    top_s = g.node(unreal.MaterialExpressionStep, 3, const_y=0.95)  # 1 where 0.95 <= v
    g.link(v, "", top_s, "X")
    m = bars
    for other in (posts, bottom, top_s):
        mx = g.node(unreal.MaterialExpressionMax, 2)
        g.link(m, "", mx, "A")
        g.link(other, "", mx, "B")
        m = mx
    g.out(m, "", unreal.MaterialProperty.MP_OPACITY_MASK)
    g.out(g.node(unreal.MaterialExpressionVertexColor, 2), "", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(g.const(0.45), "", unreal.MaterialProperty.MP_ROUGHNESS)
    g.out(g.const(0.6), "", unreal.MaterialProperty.MP_METALLIC)
    mat = g.save()
    mat.set_editor_property("opacity_mask_clip_value", 0.5)
    eal.save_loaded_asset(mat)
    return mat


def import_walls(sectors=None):
    """Garden walls (prep_objects.garden_walls): the building finishes by slot name, coping in the concrete finish,
    railings in vertex-coloured paint. Nanite with the full-detail fallback; collision (cars hit them)."""
    mats = {name: unreal.load_asset(f"{ROOT}/Materials/Buildings/M_{name}") for name in FINISHES}
    mats["coping"] = mats["wall_concrete"]
    mats["rail"] = railing_material()
    block = json.load(open(os.path.join(CACHE, "block.json")))
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    done = 0
    for si, sj in sectors or block["sectors"]:
        key = f"{si}_{sj}"
        path = os.path.join(CACHE, "sectors", key, "walls.glb")
        for a in eas.get_all_level_actors():
            if a.get_actor_label() == f"walls_{key}":
                eas.destroy_actor(a)
        if not os.path.exists(path):
            continue
        for mesh in import_glb(path, f"{ROOT}/Sectors/{key}", f"SM_walls_{key}"):
            for i, slot in enumerate(mesh.static_materials):
                m = mats.get(str(slot.material_slot_name))
                if m:
                    mesh.set_material(i, m)
            setup_mesh(mesh, {}, True, nanite=True)
            place(mesh, f"walls_{key}", si, sj, True)
            done += 1
    save()
    log["walls"] = done
    return done


def reimport_props(sectors=None):
    """Re-import the block's sector props (plants, hedges, bins, lamps) after prep_objects.py."""
    kinds, lamp = prop_meshes()
    block = json.load(open(os.path.join(CACHE, "block.json")))
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    n = 0
    for si, sj in sectors or block["sectors"]:
        key = f"{si}_{sj}"
        for a in eas.get_all_level_actors():
            if a.get_actor_label() == f"Props_{key}":
                eas.destroy_actor(a)
        unreal.BeratImporter.import_sector_props(None, os.path.join(CACHE, "sectors", key), si, sj, kinds, lamp,
                                                 unreal.Vector(0, 0, 360), 250000.0)
        n += 1
    save()
    return n
