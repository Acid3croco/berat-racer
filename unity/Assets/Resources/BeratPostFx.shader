// Post chain (all linear HDR in, sRGB out): bloom prefilter/blur, then radial speed blur + bloom + ACES tone map + grade + vignette.
Shader "Hidden/Berat/PostFx"
{
    Properties { _MainTex ("Texture", 2D) = "white" {} _Bloom ("Bloom", 2D) = "black" {} }
    CGINCLUDE
    #include "UnityCG.cginc"
    sampler2D _MainTex, _Bloom; float4 _MainTex_TexelSize;
    float _Strength, _Vig, _BloomIntensity, _Exposure, _Saturation, _Contrast, _Threshold;

    float3 aces (float3 x) { return saturate((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14)); }

    fixed4 fragPrefilter (v2f_img i) : SV_Target
    {   // 2x2 box downsample + soft threshold on brightness
        float2 t = _MainTex_TexelSize.xy;
        // each tap is clamped first: one over-bright sub-pixel (a pale far field, a roof edge) must not light up the whole bloom kernel and flicker as the camera moves
        float3 c = (min(tex2D(_MainTex, i.uv + t * float2(-1, -1)).rgb, 2.2) + min(tex2D(_MainTex, i.uv + t * float2(1, -1)).rgb, 2.2) + min(tex2D(_MainTex, i.uv + t * float2(-1, 1)).rgb, 2.2) + min(tex2D(_MainTex, i.uv + t * float2(1, 1)).rgb, 2.2)) * 0.25;
        float b = max(c.r, max(c.g, c.b));
        float w = saturate((b - _Threshold) / max(_Threshold, 1e-3));
        return fixed4(c * w * w * (3.0 - 2.0 * w) , 1);
    }
    fixed4 blur (v2f_img i, float2 dir)
    {
        float2 t = _MainTex_TexelSize.xy * dir;
        float3 c = tex2D(_MainTex, i.uv).rgb * 0.2270270;
        c += (tex2D(_MainTex, i.uv + t * 1.3846).rgb + tex2D(_MainTex, i.uv - t * 1.3846).rgb) * 0.3162162;
        c += (tex2D(_MainTex, i.uv + t * 3.2308).rgb + tex2D(_MainTex, i.uv - t * 3.2308).rgb) * 0.0702703;
        return fixed4(c, 1);
    }
    fixed4 fragBlurH (v2f_img i) : SV_Target { return blur(i, float2(1, 0)); }
    fixed4 fragBlurV (v2f_img i) : SV_Target { return blur(i, float2(0, 1)); }

    fixed4 fragFinal (v2f_img i) : SV_Target
    {
        float2 dir = i.uv - 0.5; float d = length(dir);
        float3 c;
        if (_Strength > 0.005)
        {
            float2 stepv = dir * _Strength * saturate(d * 2.0 - 0.15) * 0.10;
            c = 0; for (int k = 0; k < 8; k++) c += tex2D(_MainTex, i.uv - stepv * (k / 7.0)).rgb; c /= 8.0;
        }
        else c = tex2D(_MainTex, i.uv).rgb;
        c += tex2D(_Bloom, i.uv).rgb * _BloomIntensity;
        c = aces(c * _Exposure);
        float l = dot(c, float3(0.2126, 0.7152, 0.0722));
        c = lerp(l.xxx, c, _Saturation);
        c = (c - 0.5) * _Contrast + 0.5;
        c *= lerp(float3(0.98, 1.0, 1.03), float3(1.04, 1.0, 0.93), saturate(l * 1.4));      // cool shadows, warm highlights
        c *= 1.0 - saturate(d * d * 2.2) * _Vig;
        return fixed4(pow(saturate(c), 1.0 / 2.2), 1);
    }
    ENDCG
    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment fragPrefilter
            ENDCG
        }
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment fragBlurH
            ENDCG
        }
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment fragBlurV
            ENDCG
        }
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment fragFinal
            ENDCG
        }
    }
}
