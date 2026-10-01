// Faceted, vertex-coloured, lit surface for the whole world. Normals come from screen-space derivatives so meshes can share vertices
// and still look low-poly. Real sun + cascaded soft shadows, gradient sky ambient, glossy paint / glass (vertex alpha = 255 - gloss),
// optional fine surface noise, optional emission. Colours are converted to linear here; a post pass tone-maps and encodes to sRGB.
Shader "Berat/FlatColor"
{
    Properties
    {
        _OffsetFactor ("Depth offset factor", Float) = 0
        _OffsetUnits ("Depth offset units", Float) = 0
        _Noise ("Surface noise amount", Float) = 0
        _NoiseScale ("Surface noise scale", Float) = 0.8
        _Emission ("Emission", Float) = 0
        _Detail ("Procedural material detail (tarmac, grass, tiles, brick)", Float) = 1
        _HoleRadius ("Far terrain: hole radius around the player", Float) = 0
        _HoleCenter ("Far terrain: hole centre (xz)", Vector) = (0,0,0,0)
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" "Queue"="Geometry" }
        Cull Off

        Pass
        {
            Tags { "LightMode"="ForwardBase" }
            Offset [_OffsetFactor], [_OffsetUnits]
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_fwdbase
            #pragma multi_compile_fog
            #include "UnityCG.cginc"
            #include "Lighting.cginc"
            #include "AutoLight.cginc"

            float _Noise, _NoiseScale, _Emission, _HoleRadius, _Detail; float4 _HoleCenter;

            struct appdata { float4 vertex : POSITION; fixed4 color : COLOR; float3 ground : TEXCOORD1; };
            // ground: the terrain's class (tools/ground.py) and row direction byte, flat over each triangle (a mix of two classes means nothing);
            // gw: the pattern's weight, interpolated (0 at a patch's border and on the verge, so patterns fade out instead of ending on triangle edges)
            struct v2f { float4 pos : SV_POSITION; fixed4 col : COLOR; float3 wp : TEXCOORD0; SHADOW_COORDS(1) UNITY_FOG_COORDS(2) nointerpolation float2 ground : TEXCOORD3; float gw : TEXCOORD4; };

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.col = v.color; o.ground = v.ground.xy; o.gw = v.ground.z;
                // camera-relative position: large world coordinates (tens of km from the origin) have too little float precision for the screen-space-derivative normals
                o.wp = mul((float3x3)unity_ObjectToWorld, v.vertex.xyz) + (float3(unity_ObjectToWorld[0].w, unity_ObjectToWorld[1].w, unity_ObjectToWorld[2].w) - _WorldSpaceCameraPos);
                TRANSFER_SHADOW(o);
                UNITY_TRANSFER_FOG(o, o.pos);
                return o;
            }

            float h31 (float3 p) { p = frac(p * 0.1031); p += dot(p, p.yzx + 33.33); return frac((p.x + p.y) * p.z); }
            float vnoise (float3 x)
            {
                float3 i = floor(x), f = frac(x); f = f * f * (3.0 - 2.0 * f);
                return lerp(lerp(lerp(h31(i), h31(i + float3(1,0,0)), f.x), lerp(h31(i + float3(0,1,0)), h31(i + float3(1,1,0)), f.x), f.y),
                            lerp(lerp(h31(i + float3(0,0,1)), h31(i + float3(1,0,1)), f.x), lerp(h31(i + float3(0,1,1)), h31(i + float3(1,1,1)), f.x), f.y), f.z);
            }

            // ground classes of tools/ground.py
            #define G_MEADOW 1
            #define G_CEREAL 2
            #define G_ROWCROP 3
            #define G_VINEYARD 4
            #define G_ORCHARD 5
            #define G_FALLOW 6
            #define G_PARKING 7
            #define G_YARD 9
            #define G_FOREST 10
            #define G_PITCH 12

            /// a stripe profile across rows `spacing` apart: 1 on the line (half width `w` of the spacing), 0 between, faded out with its own screen size
            float Rows(float across, float spacing, float w)
            {
                float t = across / spacing, f = abs(frac(t) - 0.5) * 2.0;
                return (1.0 - smoothstep(w, w + 0.15, 1.0 - f)) * saturate(1.0 - fwidth(t) * 2.5);
            }

            float3 GroundMaterial(float3 albedo, float2 ground, float gw, float3 wabs, float3 wq, float camDist)
            {
                int cls = (int)round(ground.x);
                float ang = (ground.y - 1.0) / 254.0 * 3.14159265;
                float2 along = float2(cos(ang), sin(ang)), side = float2(-along.y, along.x);
                float across = dot(wabs.xz, side);                               // metres across the rows (world position: stripes stay continuous)
                float3 soil = GammaToLinearSpace(float3(0.47, 0.38, 0.29)), asphalt = GammaToLinearSpace(float3(0.36, 0.36, 0.37));
                float near = saturate(1.0 - (camDist - 30.0) / 250.0) * gw;
                if (cls == G_ROWCROP && ground.y > 0.5)
                {   // maize / sunflower: crop rows 0.8 m apart, bare soil showing between them
                    float r = Rows(across, 0.8, 0.45);
                    albedo = lerp(albedo, lerp(soil, albedo * 1.12, r), 0.45 * near);
                }
                else if (cls == G_CEREAL && ground.y > 0.5)
                {   // wheat / barley: fine drill lines up close, tractor tramlines (two wheel tracks 1.8 m apart every 24 m) from afar
                    float drill = Rows(across, 0.17, 0.5);
                    float t = frac(across / 24.0) * 24.0;
                    float tram = (1.0 - smoothstep(0.25, 0.4, abs(t - 0.9))) + (1.0 - smoothstep(0.25, 0.4, abs(t - 2.7)));
                    albedo *= 1.0 - drill * 0.08 * gw * saturate(1.0 - (camDist - 10.0) / 40.0);
                    albedo = lerp(albedo, soil, tram * 0.35 * gw * saturate(1.0 - fwidth(across / 24.0) * 40.0));
                }
                else if (cls == G_VINEYARD && ground.y > 0.5)
                {   // bare or grassed strips between the vine rows (the rows themselves are meshes)
                    float row = Rows(across, 2.2, 0.35);
                    albedo = lerp(albedo, soil, (1.0 - row) * 0.5 * near);
                }
                else if (cls == G_ORCHARD && ground.y > 0.5)
                {   // mown alleys between the tree rows
                    albedo *= 1.0 + (Rows(across, 5.0, 0.4) - 0.5) * 0.12 * near;
                }
                else if (cls == G_FALLOW)
                {
                    albedo *= 1.0 + (vnoise(wq * 0.35) - 0.5) * 0.25 * gw;
                }
                else if (cls == G_PARKING)
                {   // asphalt, whatever the photo caught on it (parked cars)
                    float speck = (h31(floor(wq * float3(46.0, 1.0, 46.0))) - 0.5) * 0.12 * saturate(1.0 - (camDist - 6.0) / 30.0);
                    albedo = asphalt * (1.0 + speck + (vnoise(wq * 0.45) - 0.5) * 0.10);
                }
                else if (cls == G_YARD)
                {   // gravel and concrete: greyer, speckled
                    float grey = dot(albedo, float3(0.3, 0.55, 0.15));
                    albedo = lerp(albedo, float3(grey, grey, grey) * 1.05, 0.6) * (1.0 + (h31(floor(wq * float3(20.0, 1.0, 20.0))) - 0.5) * 0.18 * saturate(1.0 - (camDist - 6.0) / 30.0));
                }
                else if (cls == G_FOREST)
                {   // leaf litter under the canopy
                    albedo = lerp(albedo, soil * 0.8, 0.35) * (1.0 + (vnoise(wq * 0.5) - 0.5) * 0.2);
                }
                else if (cls == G_PITCH)
                {   // mowing stripes 5 m wide
                    albedo *= 1.0 + (step(0.5, frac(wabs.x / 10.0)) - 0.5) * 0.10 * gw;
                }
                return albedo;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                if (_HoleRadius > 0.0) { float2 dh = i.wp.xz + _WorldSpaceCameraPos.xz - _HoleCenter.xz; if (dot(dh, dh) < _HoleRadius * _HoleRadius) discard; }       // far terrain: the detailed chunks cover this disc
                float3 n = normalize(cross(ddy(i.wp), ddx(i.wp)));
                float3 V = normalize(-i.wp);
                n *= sign(dot(n, V));                                           // always face the viewer
                float3 albedo = GammaToLinearSpace(i.col.rgb);
                float3 wabs = i.wp + _WorldSpaceCameraPos;
                float3 wq = float3(fmod(wabs.x, 2048.0), wabs.y, fmod(wabs.z, 2048.0));      // noise coordinates: float precision at tens of km from the origin would turn every hash into static
                float camDist = length(i.wp);
                float grainFade = saturate(1.0 - (camDist - 40.0) / 180.0);            // fine grain would alias into shimmer at range
                if (_Noise > 0 && grainFade > 0.01)
                {
                    float nz = vnoise(wq * _NoiseScale) * 0.65 + vnoise(wq * _NoiseScale * 3.7 + 11.0) * 0.35;
                    albedo *= 1.0 + _Noise * grainFade * (nz - 0.5) * 2.0;
                }
                if (_Detail > 0 && i.col.a > 0.9)
                {   // procedural material detail, chosen from the vertex colour and slope (no texture assets): tarmac, grass, roof tiles, brick / render
                    float mx = max(albedo.r, max(albedo.g, albedo.b)), mn = min(albedo.r, min(albedo.g, albedo.b));
                    float chroma = (mx - mn) / max(mx, 1e-3);
                    float isGreen = smoothstep(0.02, 0.06, albedo.g - max(albedo.r, albedo.b) * 1.1);
                    float isGrey = (1.0 - smoothstep(0.10, 0.22, chroma)) * (1.0 - isGreen);
                    float isWarm = smoothstep(0.25, 0.5, chroma) * (1.0 - isGreen);
                    float flatUp = smoothstep(0.82, 0.96, n.y), roofy = smoothstep(0.2, 0.35, n.y) * (1.0 - smoothstep(0.75, 0.9, n.y)), wall = 1.0 - smoothstep(0.15, 0.3, abs(n.y));
                    float d = 1.0;
                    // tarmac: aggregate speckle up close, patched repairs further out
                    float f1 = saturate(1.0 - (camDist - 6.0) / 30.0), f2 = saturate(1.0 - (camDist - 30.0) / 150.0);
                    d += isGrey * flatUp * (mx < 0.35 ? 1.0 : 0.4) * ((h31(floor(wq * float3(46.0, 1.0, 46.0))) - 0.5) * 0.16 * f1 + (vnoise(wq * 0.45) - 0.5) * 0.14 * f2);
                    // grass: blade streaks and broad mottling
                    float g1 = saturate(1.0 - (camDist - 8.0) / 60.0), g2 = saturate(1.0 - (camDist - 40.0) / 400.0);
                    d += isGreen * flatUp * ((vnoise(float3(wq.x * 7.0, 0.0, wq.z * 2.3)) - 0.5) * 0.18 * g1 + (vnoise(wq * 0.06) - 0.5) * 0.22 * g2);
                    // roof tiles: courses across the slope, offset every other row, each tile slightly different
                    float2 tang = normalize(float2(-n.z, n.x) + 1e-4);
                    float th = dot(wq.xz, tang), rows = wq.y * 3.0 / max(0.35, 1.0 - n.y * n.y);
                    float rf = frac(rows), cf = frac(th * 2.6 + floor(rows) * 0.5);
                    float tileFade = saturate(1.0 - (camDist - 15.0) / 55.0) * saturate(1.0 - fwidth(rows) * 1.6);
                    float mortar = (1.0 - smoothstep(0.0, 0.10, rf)) * 0.5 + (1.0 - smoothstep(0.0, 0.07, cf)) * 0.3;
                    d += isWarm * roofy * tileFade * (-mortar * 0.28 + (h31(float3(floor(th * 2.6 + floor(rows) * 0.5), floor(rows), 3.0)) - 0.5) * 0.14);
                    // walls: brick courses on warm walls, faint render streaks on pale ones
                    float wrows = wq.y * 6.5, wt = dot(wq.xz, normalize(float2(-n.z, n.x) + 1e-4));
                    float wf = saturate(1.0 - (camDist - 10.0) / 40.0) * saturate(1.0 - fwidth(wrows) * 1.6);
                    float bm = (1.0 - smoothstep(0.0, 0.12, frac(wrows))) * 0.6 + (1.0 - smoothstep(0.0, 0.05, frac(wt * 4.0 + floor(wrows) * 0.5))) * 0.4;
                    d += wall * wf * (isWarm * smoothstep(0.12, 0.25, mx) * (-bm * 0.22 + (h31(float3(floor(wt * 4.0 + floor(wrows) * 0.5), floor(wrows), 7.0)) - 0.5) * 0.10)
                                      + (1.0 - isWarm * smoothstep(0.12, 0.25, mx)) * (vnoise(float3(wt * 1.5, wq.y * 0.35, 0.0)) - 0.5) * 0.10);
                    albedo *= d;
                }
                if (_Detail > 0 && i.ground.x > 0.5) albedo = GroundMaterial(albedo, i.ground, saturate(i.gw), wabs, wq, camDist);
                float gloss = 1.0 - i.col.a;                                    // alpha 255 = matte
                float3 L = normalize(_WorldSpaceLightPos0.xyz);
                UNITY_LIGHT_ATTENUATION(atten, i, i.wp);
                float ndl = saturate(dot(n, L));
                float3 amb = ShadeSH9(float4(n, 1));
                float3 sun = _LightColor0.rgb * atten;
                float3 col = albedo * (amb + sun * ndl);
                if (gloss > 0.01)
                {   // glossy paint / glass: sun glint + sky reflection with a fresnel edge
                    float3 H = normalize(L + V);
                    float spec = pow(saturate(dot(n, H)), lerp(10.0, 260.0, gloss)) * gloss * ndl;
                    float fres = pow(1.0 - saturate(dot(n, V)), 4.0);
                    float3 refl = ShadeSH9(float4(reflect(-V, n), 1)) * 1.6;
                    col += spec * sun * 2.0 + refl * (gloss * 0.035 + fres * gloss * 0.30) * (0.4 + 0.6 * atten);
                }
                col += albedo * _Emission;
                fixed4 c = fixed4(col, 1);
                UNITY_APPLY_FOG(i.fogCoord, c);
                return c;
            }
            ENDCG
        }

        Pass
        {
            Name "ShadowCaster"
            Tags { "LightMode"="ShadowCaster" }
            ZWrite On ZTest LEqual
            CGPROGRAM
            #pragma vertex vertS
            #pragma fragment fragS
            #pragma multi_compile_shadowcaster
            #include "UnityCG.cginc"
            struct v2fS { V2F_SHADOW_CASTER; };
            v2fS vertS (appdata_base v) { v2fS o; TRANSFER_SHADOW_CASTER_NORMALOFFSET(o) return o; }
            float4 fragS (v2fS i) : SV_Target { SHADOW_CASTER_FRAGMENT(i) }
            ENDCG
        }
    }
}
