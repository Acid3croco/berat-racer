// Procedural sky: gradient, sun disc + glow (follows the scene's directional light), drifting stylised clouds. Output is linear HDR.
Shader "Berat/Sky"
{
    SubShader
    {
        Tags { "Queue"="Background" "RenderType"="Background" "PreviewType"="Skybox" }
        Cull Off ZWrite Off
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            struct v2f { float4 pos : SV_POSITION; float3 dir : TEXCOORD0; };
            v2f vert (appdata_base v) { v2f o; o.pos = UnityObjectToClipPos(v.vertex); o.dir = v.vertex.xyz; return o; }

            float h21 (float2 p) { p = frac(p * float2(123.34, 456.21)); p += dot(p, p + 45.32); return frac(p.x * p.y); }
            float n2 (float2 p)
            {
                float2 i = floor(p), f = frac(p); f = f * f * (3.0 - 2.0 * f);
                return lerp(lerp(h21(i), h21(i + float2(1,0)), f.x), lerp(h21(i + float2(0,1)), h21(i + float2(1,1)), f.x), f.y);
            }
            float fbm (float2 p) { float a = 0.5, s = 0; for (int k = 0; k < 4; k++) { s += a * n2(p); p = p * 2.03 + 7.1; a *= 0.5; } return s; }

            fixed4 frag (v2f i) : SV_Target
            {
                float3 d = normalize(i.dir);
                float3 L = normalize(_WorldSpaceLightPos0.xyz);
                float h = d.y;
                float3 zenith = float3(0.045, 0.16, 0.50), horizon = float3(0.42, 0.62, 0.90), ground = float3(0.24, 0.30, 0.30);
                float3 sky = lerp(horizon, zenith, pow(saturate(h), 0.5));
                sky = lerp(sky, ground, saturate(-h * 4.0));
                float sd = saturate(dot(d, L));
                sky += float3(1.0, 0.72, 0.42) * (pow(sd, 6.0) * 0.16 + pow(sd, 48.0) * 0.5);                 // warm haze around the sun
                sky += float3(1.0, 0.95, 0.85) * smoothstep(0.99935, 0.9998, sd) * 9.0;                           // sun disc (HDR: blooms)
                if (h > 0.0)
                {
                    float2 p = d.xz / (h + 0.22) * 1.35 + float2(_Time.y * 0.006, _Time.y * 0.002);
                    float dens = fbm(p);
                    float cl = smoothstep(0.50, 0.78, dens) * saturate(h * 5.0);
                    float3 cloudLit = lerp(float3(0.62, 0.68, 0.78), float3(1.15, 1.08, 0.98), saturate(dot(d, L) * 0.5 + 0.55));
                    sky = lerp(sky, cloudLit, cl * 0.9);
                }
                return fixed4(sky, 1);
            }
            ENDCG
        }
    }
}
