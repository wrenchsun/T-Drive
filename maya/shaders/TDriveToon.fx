// TDriveToon.fx — Maya Viewport 2.0 (DirectX 11) 用 dx11Shader ラッパー
//
// 式は ../../shaders/ToonCore.hlsl にだけ書く（Unity の MS2026/Toon と共有）。ここにはサンプリング・行列・パス構成だけを書く。
// uniform 名 = Unity プロパティ名から先頭の "_" を除いたもの（maya/scripts/tdrive_toon/params.py）。
// Preview* / *Enabled はプレビュー専用で Look データには保存されない。

#include "../../shaders/ToonCore.hlsl"

// ------------------------------------------------------------------ 行列
float4x4 gWVP   : WorldViewProjection   < string UIWidget = "None"; >;
float4x4 gWorld : World                 < string UIWidget = "None"; >;
float4x4 gWIT   : WorldInverseTranspose < string UIWidget = "None"; >;
float4x4 gVP    : ViewProjection        < string UIWidget = "None"; >;
float4x4 gViewI : ViewInverse           < string UIWidget = "None"; >;

// ------------------------------------------------------------------ Common（D-Drive MaterialCommon）
Texture2D BaseMap < string UIGroup = "Common"; string ResourceName = ""; string UIWidget = "FilePicker"; string UIName = "Base Map"; string ResourceType = "2D"; int UIOrder = 1; >;
bool BaseMapEnabled < string UIGroup = "Common"; string UIName = "Base Map Enabled"; int UIOrder = 2; > = false;
float4 BaseColor < string UIGroup = "Common"; string UIName = "Base Color"; string UIWidget = "ColorPicker"; int UIOrder = 3; > = {1.0, 1.0, 1.0, 1.0};
bool AlphaClip < string UIGroup = "Common"; string UIName = "Alpha Clip (Cutout)"; int UIOrder = 4; > = false;
float Cutoff < string UIGroup = "Common"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 5; > = 0.5;

// ------------------------------------------------------------------ Specific（_Toon*）
float4 ToonShadeColor < string UIGroup = "Shadow"; string UIWidget = "ColorPicker"; int UIOrder = 10; > = {0.78, 0.72, 0.86, 1.0};
float ToonShadeThreshold < string UIGroup = "Shadow"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 11; > = 0.5;
float ToonShadeFeather < string UIGroup = "Shadow"; float UIMin = 0.001; float UIMax = 0.5; int UIOrder = 12; > = 0.02;
float ToonShadowStrength < string UIGroup = "Shadow"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 13; > = 1.0;

Texture2D ToonMaskMap < string UIGroup = "Mask"; string ResourceName = ""; string UIWidget = "FilePicker"; string ResourceType = "2D"; int UIOrder = 20; >;
bool ToonMaskMapEnabled < string UIGroup = "Mask"; int UIOrder = 21; > = false;
bool VertexMaskEnabled < string UIGroup = "Mask"; string UIName = "Vertex Mask Enabled (tdToonMask)"; int UIOrder = 22; > = false;

float4 ToonTintColor < string UIGroup = "Tint"; string UIWidget = "ColorPicker"; int UIOrder = 30; > = {1.0, 0.6, 0.6, 1.0};
float ToonTintStrength < string UIGroup = "Tint"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 31; > = 0.0;

float4 ToonOutlineColor < string UIGroup = "Outline"; string UIWidget = "ColorPicker"; int UIOrder = 40; > = {0.28, 0.2, 0.2, 1.0};
float ToonOutlineBaseMix < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 41; > = 0.5;
float ToonOutlineWidth < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 10.0; int UIOrder = 42; > = 1.0;
float ToonOutlineSmoothNormal < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 43; > = 1.0;

// ------------------------------------------------------------------ プレビュー環境（Unity では キャラクターライト / ポスト）
float3 PreviewLightDir < string UIGroup = "Preview"; string UIName = "Character Light Dir (to light, world)"; int UIOrder = 90; > = {0.4, 0.6, 0.7};
float3 PreviewLightColor < string UIGroup = "Preview"; string UIWidget = "ColorPicker"; int UIOrder = 91; > = {1.0, 1.0, 1.0};
int PreviewTonemap < string UIGroup = "Preview"; string UIName = "Tonemap"; string UIFieldNames = "None:Neutral"; int UIOrder = 92; > = 1;
// チャンネル単体表示・不具合調査用（docs/05 §3「チャンネル単体表示」）
int PreviewDebug < string UIGroup = "Preview"; string UIName = "Debug View"; string UIFieldNames = "Off:UV0:Normal:Lit:Mask R:Mask G:Mask B:Mask A:Base Map:Smooth Normal UV2"; int UIOrder = 93; > = 0;

SamplerState SamLinearWrap { Filter = MIN_MAG_MIP_LINEAR; AddressU = Wrap; AddressV = Wrap; };

// ------------------------------------------------------------------ 描画ステート（Opaque テクニックは overridesDrawState = true）
RasterizerState RS_CullBack  { CullMode = Back; };
RasterizerState RS_CullFront { CullMode = Front; };
RasterizerState RS_CullNone  { CullMode = None; };
DepthStencilState DS_Default { DepthEnable = true; DepthWriteMask = ALL; DepthFunc = LESS_EQUAL; };
BlendState BS_Opaque { BlendEnable[0] = false; RenderTargetWriteMask[0] = 0x0F; };

// ------------------------------------------------------------------ 頂点入出力
struct VSIn
{
    float3 position : POSITION;
    float3 normal   : NORMAL;
    float4 tangent  : TANGENT;   // w = UV の裏返り
    float2 uv       : TEXCOORD0;
    float2 uv2      : TEXCOORD2; // tdSmoothNormal（八面体エンコード・接空間）
    float4 color    : COLOR0;    // tdToonMask
};

struct VSOut
{
    float4 positionCS : SV_Position;
    float2 uv         : TEXCOORD0;
    float3 normalWS   : TEXCOORD1;
    float3 positionWS : TEXCOORD2;
    float4 vertexMask : TEXCOORD3;  // 頂点カラー（未使用時は白）
    float2 uv2        : TEXCOORD4;  // デバッグ表示用
};

static const float4 kWhite = float4(1.0, 1.0, 1.0, 1.0);

float4 VertexMask(float4 vertexColor)
{
    return VertexMaskEnabled ? vertexColor : kWhite;
}

float4 MaskMapSample(float2 uv)
{
    return ToonMaskMapEnabled ? ToonMaskMap.Sample(SamLinearWrap, uv) : kWhite;
}

float4 MaskMapSampleLevel0(float2 uv)
{
    return ToonMaskMapEnabled ? ToonMaskMap.SampleLevel(SamLinearWrap, uv, 0) : kWhite;
}

float4 SampleBase(float2 uv)
{
    float4 c = BaseColor;
    if (BaseMapEnabled)
        c *= BaseMap.Sample(SamLinearWrap, uv);
    return c;
}

// ------------------------------------------------------------------ 本体
VSOut VS_Main(VSIn v)
{
    VSOut o;
    float4 p = float4(v.position, 1.0);
    o.positionCS = mul(p, gWVP);
    o.positionWS = mul(p, gWorld).xyz;
    o.normalWS = normalize(mul(float4(v.normal, 0.0), gWIT).xyz);
    o.uv = v.uv;
    o.vertexMask = VertexMask(v.color);
    o.uv2 = v.uv2;
    return o;
}

float4 ShadeMain(VSOut i, bool frontFace)
{
    float4 base = SampleBase(i.uv);
    if (AlphaClip && base.a < Cutoff)
        discard;
    float3 N = normalize(i.normalWS) * (frontFace ? 1.0 : -1.0);
    float3 L = normalize(PreviewLightDir);
    float4 mask = Toon_CombineMask(i.vertexMask, MaskMapSample(i.uv));
    float lit = Toon_LitFactor(N, L, mask, ToonShadeThreshold, ToonShadeFeather, ToonShadowStrength);
    if (PreviewDebug != 0)
    {
        float3 d = float3(0.0, 0.0, 0.0);
        if (PreviewDebug == 1) d = float3(frac(i.uv), 0.0);
        else if (PreviewDebug == 2) d = N * 0.5 + 0.5;
        else if (PreviewDebug == 3) d = lit.xxx;
        else if (PreviewDebug >= 4 && PreviewDebug <= 7) d = mask[PreviewDebug - 4].xxx;
        else if (PreviewDebug == 8) d = BaseMap.Sample(SamLinearWrap, i.uv).rgb;
        else if (PreviewDebug == 9) d = float3(i.uv2 * 0.5 + 0.5, 0.0);
        return float4(d, 1.0);
    }
    float3 col = Toon_Shade(base.rgb, lit, ToonShadeColor.rgb, PreviewLightColor);
    col = Toon_ApplyTint(col, mask, ToonTintColor.rgb, ToonTintStrength);
    col = Toon_Tonemap(col, PreviewTonemap);
    return float4(col, base.a);
}

float4 PS_Opaque(VSOut i, bool frontFace : SV_IsFrontFace) : SV_Target
{
    return ShadeMain(i, frontFace);
}

// Maya の透明描画はプリマルチプライドα前提
float4 PS_Transparent(VSOut i, bool frontFace : SV_IsFrontFace) : SV_Target
{
    float4 c = ShadeMain(i, frontFace);
    return float4(c.rgb * c.a, c.a);
}

// ------------------------------------------------------------------ アウトライン（背面法線押し出し）
VSOut VS_Outline(VSIn v)
{
    VSOut o;
    float4 p = float4(v.position, 1.0);
    float3 normalWS = normalize(mul(float4(v.normal, 0.0), gWIT).xyz);
    float3 n = normalWS;
    if (ToonOutlineSmoothNormal > 0.5 && any(v.uv2 != float2(0.0, 0.0)))
    {
        float3 tangentWS = normalize(mul(float4(v.tangent.xyz, 0.0), gWorld).xyz);
        n = Toon_SmoothNormalWS(v.uv2, normalWS, tangentWS, v.tangent.w < 0.0 ? -1.0 : 1.0);
    }
    float4 mask = Toon_CombineMask(VertexMask(v.color), MaskMapSampleLevel0(v.uv));
    float4 clip = mul(p, gWVP);
    float2 nClip = mul(float4(n, 0.0), gVP).xy;
    clip.xy += Toon_OutlineClipOffset(nClip, ToonOutlineWidth, mask, clip.w);
    o.positionCS = clip;
    o.positionWS = mul(p, gWorld).xyz;
    o.normalWS = normalWS;
    o.uv = v.uv;
    o.vertexMask = mask;  // アウトラインでは頂点で合成済みのマスクを渡す
    o.uv2 = v.uv2;
    return o;
}

float4 PS_Outline(VSOut i) : SV_Target
{
    if (ToonOutlineWidth <= 0.0 || i.vertexMask.g <= 0.0)
        discard;
    float4 base = SampleBase(i.uv);
    if (AlphaClip && base.a < Cutoff)
        discard;
    float3 col = Toon_OutlineColor(base.rgb, ToonOutlineColor.rgb, ToonOutlineBaseMix);
    return float4(Toon_Tonemap(col, PreviewTonemap), 1.0);
}

// ------------------------------------------------------------------ テクニック
technique11 Opaque
<
    bool overridesDrawState = true;  // アウトラインパスの Cull Front を自前で設定する
    int isTransparent = 0;
>
{
    pass Outline < string drawContext = "colorPass"; >
    {
        SetVertexShader(CompileShader(vs_5_0, VS_Outline()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, PS_Outline()));
        SetRasterizerState(RS_CullFront);
        SetDepthStencilState(DS_Default, 0);
        SetBlendState(BS_Opaque, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
    }
    pass Main < string drawContext = "colorPass"; >
    {
        SetVertexShader(CompileShader(vs_5_0, VS_Main()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, PS_Opaque()));
        SetRasterizerState(RS_CullBack);
        SetDepthStencilState(DS_Default, 0);
        SetBlendState(BS_Opaque, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
    }
}

technique11 OpaqueDoubleSided
<
    bool overridesDrawState = true;  // アウトラインパスの Cull Front を自前で設定する
    int isTransparent = 0;
>
{
    pass Outline < string drawContext = "colorPass"; >
    {
        SetVertexShader(CompileShader(vs_5_0, VS_Outline()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, PS_Outline()));
        SetRasterizerState(RS_CullFront);
        SetDepthStencilState(DS_Default, 0);
        SetBlendState(BS_Opaque, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
    }
    pass Main < string drawContext = "colorPass"; >
    {
        SetVertexShader(CompileShader(vs_5_0, VS_Main()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, PS_Opaque()));
        SetRasterizerState(RS_CullNone);
        SetDepthStencilState(DS_Default, 0);
        SetBlendState(BS_Opaque, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
    }
}

technique11 Transparent
<
    bool overridesDrawState = false;  // Maya の透明描画ステート（プリマルチプライドα）に任せる
    int isTransparent = 1;
>
{
    pass Main < string drawContext = "colorPass"; >
    {
        SetVertexShader(CompileShader(vs_5_0, VS_Main()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, PS_Transparent()));
    }
}
