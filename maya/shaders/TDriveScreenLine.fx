// TDriveScreenLine.fx — 画面上の線（T-23 インナーライン / T-42 外側輪郭）の合成。Maya の Render Override（screen_line.py）専用。
//
// 判定式は ../../shaders/ToonCore.hlsl の Toon_Line*（Unity の ToonRendererFeature と共有。docs/03 §10）。
// 入力: gColorTex = 本体の描画結果、gIdTex = ToonId（本体パスが 2 枚目の描画先に書いたもの）。画素位置で読む（Load）。

#include "../../shaders/ToonCore.hlsl"

Texture2D gColorTex;
Texture2D gIdTex;
Texture2D gOverlayIdTex;  // 透かしバッファ（透かし線の部位だけを描いた ToonId。T-44）
SamplerState gPointSampler;  // 未使用（Maya の効果ファイルの読み込み要件で 1 つ置く）

float2 gScreenSize = {1920.0, 1080.0};
int gTonemap = 1;
bool gInnerEnabled = false;
float gInnerRadius = 1.0;                // 画素（Toon_LineRadius 済み）
float3 gInnerColor = {0.0, 0.0, 0.0};    // リニア
bool gSeeThroughEnabled = false;
float gSeeThroughRadius = 1.0;
float3 gSeeThroughColor = {0.0, 0.0, 0.0};
float gSeeThroughMaxDist = 0.1;  // m
int gSeeThroughOccluders = 0;     // 透かす手前の部位のビットマスク
bool gOuterEnabled = false;
float gOuterRadius = 1.0;
float3 gOuterColor = {0.0, 0.0, 0.0};

struct VSIn  { float3 position : POSITION; float3 uv : TEXCOORD0; };
struct VSOut { float4 position : SV_Position; };

VSOut VS_Quad(VSIn i)
{
    VSOut o;
    o.position = float4(i.position, 1.0);
    return o;
}

float4 IdAt(int2 p)
{
    p = clamp(p, int2(0, 0), int2(gScreenSize) - 1);
    return gIdTex.Load(int3(p, 0));
}

float EdgeInner(int2 p, float4 idP, int r)
{
    float e = 0.0;
    e = max(e, Toon_LineInnerPair(idP, IdAt(p + int2(r, 0))));
    e = max(e, Toon_LineInnerPair(idP, IdAt(p - int2(r, 0))));
    e = max(e, Toon_LineInnerPair(idP, IdAt(p + int2(0, r))));
    e = max(e, Toon_LineInnerPair(idP, IdAt(p - int2(0, r))));
    return e;
}

float4 OverlayAt(int2 p)
{
    p = clamp(p, int2(0, 0), int2(gScreenSize) - 1);
    return gOverlayIdTex.Load(int3(p, 0));
}

float EdgeSeeThrough(int2 p, float4 idP, int r)
{
    float4 op = OverlayAt(p);
    float e = 0.0;
    e = max(e, Toon_LineSeeThroughPair(op, OverlayAt(p + int2(r, 0)), idP, gSeeThroughMaxDist, (uint)gSeeThroughOccluders));
    e = max(e, Toon_LineSeeThroughPair(op, OverlayAt(p - int2(r, 0)), idP, gSeeThroughMaxDist, (uint)gSeeThroughOccluders));
    e = max(e, Toon_LineSeeThroughPair(op, OverlayAt(p + int2(0, r)), idP, gSeeThroughMaxDist, (uint)gSeeThroughOccluders));
    e = max(e, Toon_LineSeeThroughPair(op, OverlayAt(p - int2(0, r)), idP, gSeeThroughMaxDist, (uint)gSeeThroughOccluders));
    return e;
}

float EdgeOuter(int2 p, float4 idP, int r)
{
    float e = 0.0;
    e = max(e, Toon_LineOuterPair(idP, IdAt(p + int2(r, 0))));
    e = max(e, Toon_LineOuterPair(idP, IdAt(p - int2(r, 0))));
    e = max(e, Toon_LineOuterPair(idP, IdAt(p + int2(0, r))));
    e = max(e, Toon_LineOuterPair(idP, IdAt(p - int2(0, r))));
    return e;
}

float4 PS_Composite(VSOut i) : SV_Target
{
    int2 p = int2(i.position.xy);
    float4 c = gColorTex.Load(int3(p, 0));
    float4 idP = IdAt(p);
    if (gOuterEnabled && EdgeOuter(p, idP, (int)gOuterRadius) > 0.5)
        c.rgb = Toon_Tonemap(gOuterColor, gTonemap);
    if (gInnerEnabled && EdgeInner(p, idP, (int)gInnerRadius) > 0.5)
        c.rgb = Toon_Tonemap(gInnerColor, gTonemap);
    if (gSeeThroughEnabled && EdgeSeeThrough(p, idP, (int)gSeeThroughRadius) > 0.5)
        c.rgb = Toon_Tonemap(gSeeThroughColor, gTonemap);  // 髪の上に出すので最後
    return c;
}

technique11 Main
{
    pass P0
    {
        SetVertexShader(CompileShader(vs_5_0, VS_Quad()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, PS_Composite()));
    }
}
