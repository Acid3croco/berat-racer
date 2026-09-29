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

            float _Noise, _NoiseScale, _Emission;

            struct appdata { float4 vertex : POSITION; fixed4 color : COLOR; };
            struct v2f { float4 pos : SV_POSITION; fixed4 col : COLOR; float3 wp : TEXCOORD0; SHADOW_COORDS(1) UNITY_FOG_COORDS(2) };

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.col = v.color;
                o.wp = mul(unity_ObjectToWorld, v.vertex).xyz;
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

            fixed4 frag (v2f i) : SV_Target
            {
                float3 n = normalize(cross(ddy(i.wp), ddx(i.wp)));
                float3 V = normalize(_WorldSpaceCameraPos - i.wp);
                n *= sign(dot(n, V));                                           // always face the viewer
                float3 albedo = GammaToLinearSpace(i.col.rgb);
                if (_Noise > 0)
                {
                    float nz = vnoise(i.wp * _NoiseScale) * 0.65 + vnoise(i.wp * _NoiseScale * 3.7 + 11.0) * 0.35;
                    albedo *= 1.0 + _Noise * (nz - 0.5) * 2.0;
                }
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
