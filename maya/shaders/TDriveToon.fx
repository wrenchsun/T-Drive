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
float4x4 gView  : View                  < string UIWidget = "None"; >;
float2 gViewportPixelSize : ViewportPixelSize < string UIWidget = "None"; >;

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
// マスクのチャンネル単位ペイント中だけ使う（Maya プレビュー専用）: 0 = なし / 1..4 = R G B A を作業用カラーセット（COLOR1）の値で置き換える
int MaskEditChannel < string UIGroup = "Mask"; string UIName = "Mask Edit Channel"; string UIFieldNames = "None:R:G:B:A"; int UIOrder = 23; > = 0;

float4 ToonTintColor < string UIGroup = "Tint"; string UIWidget = "ColorPicker"; int UIOrder = 30; > = {1.0, 0.6, 0.6, 1.0};
float ToonTintStrength < string UIGroup = "Tint"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 31; > = 0.0;

float4 ToonOutlineColor < string UIGroup = "Outline"; string UIWidget = "ColorPicker"; int UIOrder = 40; > = {0.28, 0.2, 0.2, 1.0};
float ToonOutlineBaseMix < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 41; > = 0.5;
float ToonOutlineWidth < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 10.0; int UIOrder = 42; > = 1.0;
float ToonOutlineSmoothNormal < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 43; > = 1.0;
float ToonOutlineDistanceScale < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 44; > = 0.0;
float ToonOutlineRefDistance < string UIGroup = "Outline"; float UIMin = 0.1; float UIMax = 20.0; int UIOrder = 45; > = 2.0;

// ---- P1（既定値では P0 と同じ見た目）
float ToonLightColorInfluence < string UIGroup = "Light"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 50; > = 1.0;
float ToonDepthOffset < string UIGroup = "Depth"; string UIName = "Depth Offset (m)"; float UIMin = 0.0; float UIMax = 0.2; int UIOrder = 51; > = 0.0;
float4 ToonRimColor < string UIGroup = "Rim"; string UIWidget = "ColorPicker"; int UIOrder = 52; > = {1.0, 1.0, 1.0, 1.0};
float ToonRimPower < string UIGroup = "Rim"; float UIMin = 0.5; float UIMax = 16.0; int UIOrder = 53; > = 4.0;
float ToonRimStrength < string UIGroup = "Rim"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 54; > = 0.0;
Texture2D ToonHairHighlightMap < string UIGroup = "Hair"; string ResourceName = ""; string UIWidget = "FilePicker"; string ResourceType = "2D"; int UIOrder = 55; >;
bool ToonHairHighlightMapEnabled < string UIGroup = "Hair"; int UIOrder = 56; > = false;
float4 ToonHairHighlightColor < string UIGroup = "Hair"; string UIWidget = "ColorPicker"; int UIOrder = 57; > = {1.0, 1.0, 0.95, 1.0};
float ToonHairHighlightShift < string UIGroup = "Hair"; float UIMin = -0.5; float UIMax = 0.5; int UIOrder = 58; > = 0.0;

// ---- P2（既定値では従来と同じ見た目）
float4 ToonShade2Color < string UIGroup = "Shadow"; string UIWidget = "ColorPicker"; int UIOrder = 14; > = {0.6, 0.52, 0.72, 1.0};
float ToonShade2Threshold < string UIGroup = "Shadow"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 15; > = 0.25;
float ToonShade2Strength < string UIGroup = "Shadow"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 16; > = 0.0;
float ToonSaturation < string UIGroup = "Color"; float UIMin = 0.0; float UIMax = 2.0; int UIOrder = 60; > = 1.0;
float ToonBrightness < string UIGroup = "Color"; float UIMin = 0.0; float UIMax = 2.0; int UIOrder = 61; > = 1.0;
float ToonOutlineShadowSide < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 3.0; int UIOrder = 46; > = 1.0;
float ToonOutlineBottom < string UIGroup = "Outline"; float UIMin = 0.0; float UIMax = 3.0; int UIOrder = 47; > = 1.0;
Texture2D ToonMatCapMap < string UIGroup = "MatCap"; string ResourceName = ""; string UIWidget = "FilePicker"; string ResourceType = "2D"; int UIOrder = 62; >;
bool ToonMatCapMapEnabled < string UIGroup = "MatCap"; int UIOrder = 63; > = false;
float ToonMatCapStrength < string UIGroup = "MatCap"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 64; > = 0.0;

// ------------------------------------------------------------------ プレビュー環境（Unity では キャラクターライト / ポスト）
float3 PreviewLightDir < string UIGroup = "Preview"; string UIName = "Character Light Dir (to light, world)"; int UIOrder = 90; > = {0.4, 0.6, 0.7};
float3 PreviewLightColor < string UIGroup = "Preview"; string UIWidget = "ColorPicker"; int UIOrder = 91; > = {1.0, 1.0, 1.0};
int PreviewTonemap < string UIGroup = "Preview"; string UIName = "Tonemap"; string UIFieldNames = "None:Neutral"; int UIOrder = 92; > = 1;
// Maya の長さ単位（cm）/ Unity 単位（m）。m で定義したパラメータ（DepthOffset・距離）の換算に使う
float PreviewUnitScale < string UIGroup = "Preview"; int UIOrder = 94; > = 100.0;
// T-22 奥行き圧縮のキャラクター単位の量と中心（Unity では ToonCharacter が渡す）
float PreviewDepthCompression < string UIGroup = "Preview"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 95; > = 0.0;
float3 PreviewDepthPivot < string UIGroup = "Preview"; int UIOrder = 96; > = {0.0, 0.0, 0.0};
float ToonDepthCompressWeight < string UIGroup = "Depth"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 52; > = 0.0;
// T-21 SDF 顔影マップ（顔の向きはキャラクター単位。Unity では頭のボーンから）
Texture2D ToonFaceShadowMap < string UIGroup = "FaceShadow"; string ResourceName = ""; string UIWidget = "FilePicker"; string ResourceType = "2D"; int UIOrder = 70; >;
bool ToonFaceShadowMapEnabled < string UIGroup = "FaceShadow"; int UIOrder = 71; > = false;
float ToonFaceShadowWeight < string UIGroup = "FaceShadow"; float UIMin = 0.0; float UIMax = 1.0; int UIOrder = 72; > = 0.0;
float3 PreviewFaceForward < string UIGroup = "Preview"; int UIOrder = 97; > = {0.0, 0.0, 1.0};
float3 PreviewFaceRight < string UIGroup = "Preview"; int UIOrder = 98; > = {-1.0, 0.0, 0.0};
static const float3 kUp = float3(0.0, 1.0, 0.0);
// チャンネル単体表示・不具合調査用（docs/05 §3「チャンネル単体表示」）
int PreviewDebug < string UIGroup = "Preview"; string UIName = "Debug View"; string UIFieldNames = "Off:UV0:Normal:Lit:Mask R:Mask G:Mask B:Mask A:Base Map:Smooth Normal UV2"; int UIOrder = 93; > = 0;

SamplerState SamLinearWrap { Filter = MIN_MAG_MIP_LINEAR; AddressU = Wrap; AddressV = Wrap; };

// ------------------------------------------------------------------ 描画ステート（Opaque テクニックは overridesDrawState = true）
// Maya のメッシュは反時計回りが表面。自前ステートでは DirectX 既定（時計回り = 表面）になるため明示する
RasterizerState RS_CullBack  { CullMode = Back;  FrontCounterClockwise = true; };
RasterizerState RS_CullFront { CullMode = Front; FrontCounterClockwise = true; };
RasterizerState RS_CullNone  { CullMode = None;  FrontCounterClockwise = true; };
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
    float4 color1   : COLOR1;    // tdToonMaskEdit（チャンネル単位ペイントの作業用）
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

float4 VertexMask(float4 vertexColor, float4 editColor)
{
    float4 m = VertexMaskEnabled ? vertexColor : kWhite;
    // 作業用カラーセットは白黒で塗る。動的添字への代入は不可なので選択ベクトルで合成する
    float4 sel = float4(MaskEditChannel == 1, MaskEditChannel == 2, MaskEditChannel == 3, MaskEditChannel == 4);
    return lerp(m, editColor.rrrr, sel);
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
float3 CameraPosWS()
{
    return gViewI[3].xyz;
}

// T-09: 視線方向にカメラへ寄せたワールド位置（本体・アウトライン共通）
// T-22 奥行き圧縮 → T-09 デプスオフセットの順に適用したワールド位置（本体・アウトライン共通）
float3 OffsetPositionWS(float3 positionWS)
{
    float k = PreviewDepthCompression * ToonDepthCompressWeight;
    if (k > 0.0)
    {
        float3 posVS = mul(float4(positionWS, 1.0), gView).xyz;
        float3 pivotVS = mul(float4(PreviewDepthPivot, 1.0), gView).xyz;
        positionWS = mul(float4(Toon_DepthCompressVS(posVS, pivotVS, k), 1.0), gViewI).xyz;
    }
    return Toon_DepthOffsetWS(positionWS, CameraPosWS(), ToonDepthOffset * PreviewUnitScale);
}

VSOut VS_Main(VSIn v)
{
    VSOut o;
    float4 p = float4(v.position, 1.0);
    float3 posWS = OffsetPositionWS(mul(p, gWorld).xyz);
    o.positionCS = mul(float4(posWS, 1.0), gVP);
    o.positionWS = posWS;
    o.normalWS = normalize(mul(float4(v.normal, 0.0), gWIT).xyz);
    o.uv = v.uv;
    o.vertexMask = VertexMask(v.color, v.color1);
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
    if (ToonFaceShadowMapEnabled && ToonFaceShadowWeight > 0.0)
    {
        float value = ToonFaceShadowMap.Sample(SamLinearWrap, Toon_FaceShadowUV(i.uv, PreviewFaceRight, L)).r;
        lit = Toon_FaceShadowLit(lit, value, Toon_FaceLightAngle01(PreviewFaceForward, L), ToonShadeFeather, ToonShadowStrength, ToonFaceShadowWeight);
    }
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
    float3 V = normalize(CameraPosWS() - i.positionWS);
    float3 lightC = Toon_LightColor(PreviewLightColor, ToonLightColorInfluence);
    float lit2 = Toon_Lit2Factor(Toon_ShadeInput(N, L, mask), ToonShade2Threshold, ToonShadeFeather, ToonShade2Strength);
    float3 col = Toon_Shade2(base.rgb, lit, lit2, ToonShadeColor.rgb, ToonShade2Color.rgb, lightC);
    if (ToonMatCapMapEnabled)
    {
        float3 normalVS = normalize(mul(float4(N, 0.0), gView).xyz);
        col += ToonMatCapMap.Sample(SamLinearWrap, Toon_MatCapUV(normalVS)).rgb * ToonMatCapStrength * lightC;
    }
    col += ToonRimColor.rgb * Toon_Rim(N, V, ToonRimPower, ToonRimStrength, lit) * lightC;
    if (ToonHairHighlightMapEnabled)
    {
        float h = ToonHairHighlightMap.Sample(SamLinearWrap, Toon_HairHighlightUV(i.uv, V, kUp, ToonHairHighlightShift)).r;
        col += ToonHairHighlightColor.rgb * h * lit * lightC;
    }
    col = Toon_ApplyTint(col, mask, ToonTintColor.rgb, ToonTintStrength);
    col = Toon_ColorCorrect(col, ToonSaturation, ToonBrightness);
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
    float4 mask = Toon_CombineMask(VertexMask(v.color, v.color1), MaskMapSampleLevel0(v.uv));
    float3 posWS = OffsetPositionWS(mul(p, gWorld).xyz);
    float4 clip = mul(float4(posWS, 1.0), gVP);
    float2 nClip = mul(float4(n, 0.0), gVP).xy;
    float distM = length(CameraPosWS() - posWS) / PreviewUnitScale;
    float width = ToonOutlineWidth * Toon_OutlineDistanceFactor(distM, ToonOutlineRefDistance, ToonOutlineDistanceScale)
                * Toon_OutlineDirectionFactor(normalWS, normalize(PreviewLightDir), ToonOutlineShadowSide, ToonOutlineBottom);
    clip.xy += Toon_OutlineClipOffset(nClip, width, mask, clip.w, gViewportPixelSize);
    o.positionCS = clip;
    o.positionWS = posWS;
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
