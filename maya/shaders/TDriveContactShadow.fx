// TDriveContactShadow.fx — 接地影（T-29）の Maya プレビュー専用（4-6）。Look データにも出力にも入らない。
//
// 足元の地面に置いた板に描く。式は ../../shaders/ToonCore.hlsl の Toon_ContactShadow（Unity の ToonRendererFeature と共有）。
// 足の位置・半径はシーン単位（Python 側で m から換算して渡す）。

#include "../../shaders/ToonCore.hlsl"

float4x4 gWVP   : WorldViewProjection < string UIWidget = "None"; >;
float4x4 gWorld : World               < string UIWidget = "None"; >;

float3 ContactFootL < string UIName = "Foot L (world)"; > = {0.0, 0.0, 0.0};
float3 ContactFootR < string UIName = "Foot R (world)"; > = {0.0, 0.0, 0.0};
float ContactRadius < string UIName = "Radius (scene units)"; float UIMin = 0.0; > = 25.0;
float ContactStrength < string UIName = "Strength"; float UIMin = 0.0; float UIMax = 1.0; > = 0.5;

struct VSIn  { float3 position : POSITION; };
struct VSOut { float4 position : SV_Position; float3 positionWS : TEXCOORD0; };

VSOut VS_Main(VSIn i)
{
    VSOut o;
    o.position = mul(float4(i.position, 1.0), gWVP);
    o.positionWS = mul(float4(i.position, 1.0), gWorld).xyz;
    return o;
}

float FootShadow(float3 foot, float3 groundWS)
{
    return Toon_ContactShadow(length(foot.xz - groundWS.xz), foot.y - groundWS.y, ContactRadius, ContactStrength);
}

float4 PS_Main(VSOut i) : SV_Target
{
    float a = max(FootShadow(ContactFootL, i.positionWS), FootShadow(ContactFootR, i.positionWS));
    return float4(0.0, 0.0, 0.0, a);  // プリマルチプライドα: 地面の色 × (1 - a)
}

technique11 Main
<
    bool overridesDrawState = false;
    int isTransparent = 1;
>
{
    pass Main < string drawContext = "colorPass"; >
    {
        SetVertexShader(CompileShader(vs_5_0, VS_Main()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, PS_Main()));
    }
}
