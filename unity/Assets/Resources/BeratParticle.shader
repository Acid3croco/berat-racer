// Soft round particle (procedural falloff, no texture), vertex-coloured, alpha blended, fogged.
Shader "Berat/Particle"
{
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" }
        Blend SrcAlpha OneMinusSrcAlpha
        ZWrite Off Cull Off
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_fog
            #include "UnityCG.cginc"
            struct appdata { float4 vertex : POSITION; fixed4 color : COLOR; float2 uv : TEXCOORD0; };
            struct v2f { float4 pos : SV_POSITION; fixed4 col : COLOR; float2 uv : TEXCOORD0; UNITY_FOG_COORDS(1) };
            v2f vert (appdata v) { v2f o; o.pos = UnityObjectToClipPos(v.vertex); o.col = v.color; o.col.rgb = GammaToLinearSpace(v.color.rgb); o.uv = v.uv; UNITY_TRANSFER_FOG(o, o.pos); return o; }
            fixed4 frag (v2f i) : SV_Target
            {
                float d = length(i.uv - 0.5) * 2.0;
                float a = saturate(1.0 - d); a = a * a * (3.0 - 2.0 * a);
                fixed4 c = i.col; c.a *= a;
                UNITY_APPLY_FOG(i.fogCoord, c);
                return c;
            }
            ENDCG
        }
    }
}
