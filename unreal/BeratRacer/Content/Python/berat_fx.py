"""Wheel effects: one-shot puffs a car's wheels spawn on loose surfaces (dust on dry dirt, grey dust on gravel, grass
bits, mud clods). Stock Niagara: each is a copy of the engine's lightweight fountain template (stateless emitter, the
cheapest particles there are) turned into a short burst; the car spawns them at the contact points several times a second
(pooled), so the puffs stay where they were kicked up and make a trail. The modules are set through reflection
(BeratImporter.set_property_text: the lightweight emitter's classes are engine-internal, not reachable from Python).

    import berat_fx, importlib; importlib.reload(berat_fx); berat_fx.build()
"""

import unreal

from berat_import import Graph, ROOT, eal

B = unreal.BeratImporter
TEMPLATE = "/Niagara/DefaultAssets/Templates/Systems/FountainLightweight"
FX = f"{ROOT}/FX"

# name: colour (linear), alpha, count per puff, lifetime s, size cm (min, max), growth over life, up speed cm/s,
# cone angle, gravity cm/s2, drag, curl noise
PUFFS = {
    "Dust":   dict(colour=(0.36, 0.29, 0.20), alpha=0.7, count=5, life=(1.8, 3.0), size=(80, 160), grow=3.5,
                   speed=(80, 220), cone=70, gravity=-30, drag=1.6, noise=40),
    "Gravel": dict(colour=(0.42, 0.40, 0.37), alpha=0.6, count=4, life=(1.4, 2.4), size=(70, 130), grow=3.0,
                   speed=(80, 200), cone=70, gravity=-40, drag=1.8, noise=30),
    "Grass":  dict(colour=(0.06, 0.10, 0.025), alpha=0.9, count=8, life=(0.5, 0.9), size=(2.5, 6), grow=1.0,
                   speed=(250, 500), cone=40, gravity=-980, drag=0.6, noise=0),
    "Mud":    dict(colour=(0.07, 0.05, 0.03), alpha=1.0, count=6, life=(0.5, 0.9), size=(3, 8), grow=1.0,
                   speed=(300, 550), cone=35, gravity=-980, drag=0.4, noise=0),
}


# Distribution texts: ChannelConstantsAndRanges (and ChannelCurves) are the source of truth, Min / Max / Values are derived
# from them when the property changes (FNiagaraDistributionBase::PostEditChangeProperty).
def rng(lo, hi):
    return f"(Mode=UniformRange,ChannelConstantsAndRanges=({lo:f},{hi:f}))"


def const(*v):
    mode = "UniformConstant" if len(v) == 1 else "NonUniformConstant"
    return f"(Mode={mode},ChannelConstantsAndRanges=({','.join(f'{x:f}' for x in v)}))"


def const_full(v):
    """For distributions nested in another struct (no per-distribution update on change): derived fields written too."""
    return f"(Min={v:f},Max={v:f},Mode=UniformConstant,ChannelConstantsAndRanges=({v:f}))"


def curve(*keys):
    k = ",".join(f"(Time={t:f},Value={v:f})" for t, v in keys)
    return f"(Mode=UniformCurve,ChannelCurves=((Keys=({k}))))"


def puff_material():
    """Soft round puff: particle colour, lit as translucent volume (sun, sky and headlights light it), opacity from a
    soft disc broken by world-space noise, faded where it meets the ground (depth fade)."""
    g = Graph(f"{FX}/M_Puff", blend_mode=unreal.BlendMode.BLEND_TRANSLUCENT,
              translucency_lighting_mode=unreal.TranslucencyLightingMode.TLM_VOLUMETRIC_NON_DIRECTIONAL)
    g.m.set_editor_property("used_with_niagara_sprites", True)
    pc = g.node(unreal.MaterialExpressionParticleColor, 3)
    tc = g.node(unreal.MaterialExpressionTextureCoordinate, 3)
    centre = g.node(unreal.MaterialExpressionConstant2Vector, 3, r=0.5, g=0.5)
    dist = g.node(unreal.MaterialExpressionDistance, 2)
    g.link(tc, "", dist, "A")
    g.link(centre, "", dist, "B")
    # 1 at the centre, 0 at the rim: 1 - (d / 0.5), squared for a soft edge
    lin = g.node(unreal.MaterialExpressionOneMinus, 2)
    d2 = g.node(unreal.MaterialExpressionMultiply, 2, const_b=2.0)
    g.link(dist, "", d2, "A")
    g.link(d2, "", lin, "")
    sat = g.node(unreal.MaterialExpressionSaturate, 2)
    g.link(lin, "", sat, "")
    soft = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(sat, "", soft, "A")
    g.link(sat, "", soft, "B")
    wp = g.node(unreal.MaterialExpressionWorldPosition, 3)
    scale = g.node(unreal.MaterialExpressionDivide, 2, const_b=90.0)
    g.link(wp, "", scale, "A")
    noise = g.node(unreal.MaterialExpressionNoise, 2, scale=1.0, levels=3, output_min=0.25, output_max=1.0,
                   noise_function=unreal.NoiseFunction.NOISEFUNCTION_GRADIENT_ALU)
    g.link(scale, "", noise, "Position")
    shape = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(soft, "", shape, "A")
    g.link(noise, "", shape, "B")
    alpha = g.node(unreal.MaterialExpressionMultiply, 1)
    g.link(shape, "", alpha, "A")
    g.link(pc, "A", alpha, "B")
    fade = g.node(unreal.MaterialExpressionDepthFade, 1, fade_distance_default=40.0)
    g.link(alpha, "", fade, "Opacity")
    g.out(pc, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(fade, "", unreal.MaterialProperty.MP_OPACITY)
    g.out(g.const(1.0), "", unreal.MaterialProperty.MP_ROUGHNESS)
    return g.save()


def chunk_material():
    """Opaque-looking bits (grass, mud clods): masked round sprite in the particle colour."""
    g = Graph(f"{FX}/M_Chunk", blend_mode=unreal.BlendMode.BLEND_MASKED)
    g.m.set_editor_property("used_with_niagara_sprites", True)
    pc = g.node(unreal.MaterialExpressionParticleColor, 3)
    tc = g.node(unreal.MaterialExpressionTextureCoordinate, 3)
    centre = g.node(unreal.MaterialExpressionConstant2Vector, 3, r=0.5, g=0.5)
    dist = g.node(unreal.MaterialExpressionDistance, 2)
    g.link(tc, "", dist, "A")
    g.link(centre, "", dist, "B")
    lin = g.node(unreal.MaterialExpressionOneMinus, 2)
    d2 = g.node(unreal.MaterialExpressionMultiply, 2, const_b=2.0)
    g.link(dist, "", d2, "A")
    g.link(d2, "", lin, "")
    g.out(pc, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    g.out(lin, "", unreal.MaterialProperty.MP_OPACITY_MASK)
    g.out(g.const(0.9), "", unreal.MaterialProperty.MP_ROUGHNESS)
    m = g.save()
    m.set_editor_property("opacity_mask_clip_value", 0.3)
    eal.save_loaded_asset(m)
    return m


def module(objs, cls):
    for o in objs:
        if o.get_class().get_name() == cls:
            return o
    raise KeyError(cls)


def setp(o, name, text):
    if not B.set_property_text(o, name, text):
        raise RuntimeError(f"{o.get_class().get_name()}.{name} <- {text}")


def puff_system(name, p, material):
    path = f"{FX}/NS_Wheel{name}"
    # edited in place when it exists (every property below is set explicitly; deleting one a running editor holds fails)
    s = unreal.load_asset(path) if eal.does_asset_exist(path) else eal.duplicate_asset(TEMPLATE, path)
    objs = B.niagara_stateless_objects(s)
    em = objs[0]
    # one short loop: `count` particles over 0.05 s, then the emitter ends (the pooled component is released)
    setp(em, "SpawnInfos", f"((Type=Rate,Rate={const_full(p['count'] / 0.05)}))")
    setp(em, "EmitterState", f"(LoopCount=1,LoopDuration={const_full(0.05)},"
                             "MaxDistance=15000.000000,MaxDistanceReaction=Kill,VisibilityCullReaction=SleepAndLetParticlesFinish)")
    setp(em, "FixedBounds", "(Min=(X=-600,Y=-600,Z=-300),Max=(X=600,Y=600,Z=600),IsValid=True)")
    r, g_, b = p["colour"]
    init = module(objs, "NiagaraStatelessModule_InitializeParticle")
    setp(init, "LifetimeDistribution", rng(*p["life"]))
    setp(init, "ColorDistribution", const(r, g_, b, p["alpha"]))
    lo, hi = p["size"]
    setp(init, "SpriteSizeDistribution", rng(lo, hi))
    shape = module(objs, "NiagaraStatelessModule_ShapeLocation")
    setp(shape, "SphereRadius", rng(0, 25))
    vel = module(objs, "NiagaraStatelessModule_AddVelocity")
    setp(vel, "ConeVelocityDistribution", rng(*p["speed"]))
    setp(vel, "ConeAngle", str(p["cone"]))
    setp(module(objs, "NiagaraStatelessModule_GravityForce"), "GravityDistribution",
         const(0.0, 0.0, p["gravity"]))
    setp(module(objs, "NiagaraStatelessModule_Drag"), "DragDistribution", const(p["drag"]))
    curl = module(objs, "NiagaraStatelessModule_CurlNoiseForce")
    setp(curl, "bModuleEnabled", "True" if p["noise"] else "False")
    setp(curl, "NoiseStrength", str(p["noise"]))
    setp(curl, "NoiseFrequency", "150")
    grow = module(objs, "NiagaraStatelessModule_ScaleSpriteSize")
    if p["grow"] != 1.0:
        setp(grow, "bModuleEnabled", "True")
        k = p["grow"]
        setp(grow, "ScaleDistribution", curve((0.0, 1.0), (0.35, 0.55 + 0.45 * k), (1.0, k)))
    rot = module(objs, "NiagaraStatelessModule_SpriteRotationRate")
    setp(rot, "bModuleEnabled", "True")
    ren = module(objs, "NiagaraSpriteRendererProperties")
    B.set_property_text(ren, "Material", material.get_path_name())
    B.set_property_text(ren, "SortMode", "ViewDistance" if p["noise"] else "None")
    B.notify_changed(objs + [s])
    eal.save_loaded_asset(s)
    return s


def build():
    puff = puff_material()
    chunk = chunk_material()
    out = {}
    for name, p in PUFFS.items():
        out[name] = puff_system(name, p, chunk if p["grow"] == 1.0 else puff)
    unreal.log(f"[berat] wheel fx: {list(out)}")
    return out
