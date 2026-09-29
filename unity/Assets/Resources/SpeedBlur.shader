// Radial "zoom" blur + vignette that fade in with speed. Centre stays sharp, edges streak.
Shader "Hidden/Berat/SpeedBlur"
{
    Properties { _MainTex ("Texture", 2D) = "white" {} _Strength ("Strength", Float) = 0 _Vig ("Vignette", Float) = 0 }
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _MainTex; float _Strength; float _Vig;
            fixed4 frag (v2f_img i) : SV_Target
            {
                float2 dir = i.uv - 0.5;
                float d = length(dir);
                float2 stepv = dir * _Strength * saturate(d * 2.0 - 0.15) * 0.10;
                fixed4 c = 0;
                for (int k = 0; k < 8; k++) c += tex2D(_MainTex, i.uv - stepv * (k / 7.0));
                c /= 8;
                c.rgb *= 1.0 - saturate(d * d * 2.2) * _Vig;
                return c;
            }
            ENDCG
        }
    }
}
