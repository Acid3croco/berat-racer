// Minimap blit: tone-maps the HDR top-down render like the main post pass (ACES + sRGB encode), clips it to a circle and draws a dark ring.
Shader "Berat/MiniMap"
{
    Properties { _MainTex ("Texture", 2D) = "white" {} }
    SubShader
    {
        Tags { "Queue"="Overlay" "RenderType"="Transparent" }
        Blend SrcAlpha OneMinusSrcAlpha
        ZTest Always ZWrite Off Cull Off
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _MainTex;
            struct appdata { float4 vertex : POSITION; float2 uv : TEXCOORD0; };
            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
            v2f vert (appdata v) { v2f o; o.pos = UnityObjectToClipPos(v.vertex); o.uv = v.uv; return o; }
            float3 aces (float3 x) { return saturate((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14)); }
            fixed4 frag (v2f i) : SV_Target
            {
                float d = length(i.uv - 0.5) * 2.0;
                float3 c = tex2D(_MainTex, i.uv).rgb * 0.94;
                c = LinearToGammaSpace(aces(c));
                float ring = smoothstep(0.955, 0.965, d);
                c = lerp(c, float3(0.06, 0.07, 0.08), ring);
                float a = 1.0 - smoothstep(0.985, 1.0, d);
                return fixed4(c, a * 0.96);
            }
            ENDCG
        }
    }
}
