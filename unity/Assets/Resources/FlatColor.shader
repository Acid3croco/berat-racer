// Flat-shaded, vertex-coloured, fogged. Normals come from screen-space derivatives,
// so meshes can share vertices and still look faceted (low-poly look).
Shader "Berat/FlatColor"
{
    Properties
    {
        _OffsetFactor ("Depth offset factor", Float) = 0
        _OffsetUnits ("Depth offset units", Float) = 0
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" "Queue"="Geometry" }
        Cull Off
        Offset [_OffsetFactor], [_OffsetUnits]
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_fog
            #include "UnityCG.cginc"

            struct appdata { float4 vertex : POSITION; fixed4 color : COLOR; };
            struct v2f { float4 pos : SV_POSITION; fixed4 col : COLOR; float3 wp : TEXCOORD0; UNITY_FOG_COORDS(1) };

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.col = v.color;
                o.wp = mul(unity_ObjectToWorld, v.vertex).xyz;
                UNITY_TRANSFER_FOG(o, o.pos);
                return o;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                float3 n = normalize(cross(ddy(i.wp), ddx(i.wp)));
                n *= sign(dot(n, _WorldSpaceCameraPos - i.wp));          // always face the viewer
                float3 L = normalize(float3(0.45, 0.8, 0.35));
                float lit = saturate(dot(n, L));
                float3 rgb = i.col.rgb * (0.52 + 0.62 * lit);
                fixed4 c = fixed4(rgb, 1);
                UNITY_APPLY_FOG(i.fogCoord, c);
                return c;
            }
            ENDCG
        }
    }
}
