// Water surface: translucent, animated ripples (procedural normals), sun glint and sky reflection with a fresnel edge. Vertex colour = deep-water tint.
Shader "Berat/Water"
{
    SubShader
    {
        Tags { "Queue"="Transparent-10" "RenderType"="Transparent" "IgnoreProjector"="True" }
        Blend SrcAlpha OneMinusSrcAlpha
        ZWrite Off Cull Off
        Offset -1, -1
        Pass
        {
            Tags { "LightMode"="ForwardBase" }
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_fog
            #include "UnityCG.cginc"
            #include "Lighting.cginc"

            struct appdata { float4 vertex : POSITION; fixed4 color : COLOR; };
            struct v2f { float4 pos : SV_POSITION; fixed4 col : COLOR; float3 wp : TEXCOORD0; UNITY_FOG_COORDS(1) };

            v2f vert (appdata v)
            {
                v2f o; o.pos = UnityObjectToClipPos(v.vertex); o.col = v.color;
                o.wp = mul(unity_ObjectToWorld, v.vertex).xyz; UNITY_TRANSFER_FOG(o, o.pos); return o;
            }

            float h21 (float2 p) { p = frac(p * float2(123.34, 456.21)); p += dot(p, p + 45.32); return frac(p.x * p.y); }
            float vn (float2 x)
            {
                float2 i = floor(x), f = frac(x); f = f * f * (3.0 - 2.0 * f);
                return lerp(lerp(h21(i), h21(i + float2(1,0)), f.x), lerp(h21(i + float2(0,1)), h21(i + float2(1,1)), f.x), f.y);
            }
            float height (float2 p, float t)
            {
                return vn(p * 0.9 + float2(t * 0.35, t * 0.2)) * 0.6 + vn(p * 2.3 - float2(t * 0.5, -t * 0.3)) * 0.3 + vn(p * 5.1 + float2(t * 0.9, t * 0.7)) * 0.1;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                float3 V = normalize(_WorldSpaceCameraPos - i.wp);
                float camDist = length(_WorldSpaceCameraPos - i.wp);
                float t = _Time.y; float e = 0.12;
                float2 p = i.wp.xz;
                float h0 = height(p, t), hx = height(p + float2(e, 0), t), hz = height(p + float2(0, e), t);
                float slope = 0.16 * saturate(1.0 - camDist / 500.0);              // ripples fade with distance (they would shimmer)
                float3 n = normalize(float3(-(hx - h0) / e * slope, 1.0, -(hz - h0) / e * slope));
                float3 L = normalize(_WorldSpaceLightPos0.xyz);
                float ndv = saturate(dot(n, V));
                float fres = pow(1.0 - ndv, 4.0);
                float3 deep = GammaToLinearSpace(i.col.rgb);
                float3 amb = ShadeSH9(float4(0, 1, 0, 1));
                float3 col = deep * (amb * 0.9 + _LightColor0.rgb * 0.25 * saturate(dot(float3(0,1,0), L)));
                float3 refl = ShadeSH9(float4(reflect(-V, n), 1)) * 1.1;
                col = lerp(col, min(refl, 0.9), (0.10 + fres * 0.55) * saturate(1.0 - camDist / 2500.0));      // far water would glow as a bloom blob at grazing angles
                float3 H = normalize(L + V);
                col += pow(saturate(dot(n, H)), 300.0) * _LightColor0.rgb * 2.0 * saturate(1.0 - camDist / 700.0);   // sun glints (far water would flare into bloom blobs)
                fixed4 c = fixed4(col, saturate(0.72 + fres * 0.25));
                UNITY_APPLY_FOG(i.fogCoord, c);
                return c;
            }
            ENDCG
        }
    }
}
