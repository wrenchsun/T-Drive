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
// 輪郭線のスクリーンスペース（4-15）: 部位番号 → 半径（画素。0 = 描かない）・線色（リニア）・ベース色の混ぜ具合
#define SIL_PARTS 64
bool gSilEnabled = false;
int gSilMaxRadius = 1;
float gSilRadius[SIL_PARTS];
float gSilR[SIL_PARTS];
float gSilG[SIL_PARTS];
float gSilB[SIL_PARTS];
float gSilMix[SIL_PARTS];
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

int SilPart(float part)
{
    int i = (int)part;
    return (i > 0 && i < SIL_PARTS && gSilRadius[i] > 0.5) ? i : 0;
}

// 輪郭線のスクリーンスペース: 線の持ち主の部位（0 = 線なし）と持ち主の画素を返す（docs/03 §10.4）
int EdgeSilhouette(int2 p, float4 idP, out int2 owner)
{
    const int2 dirs[4] = { int2(1, 0), int2(-1, 0), int2(0, 1), int2(0, -1) };
    owner = p;
    int part = SilPart(floor(idP.b * 0.5 + 0.25));
    if (idP.b > 1.5 && part > 0)
    {
        int r = (int)gSilRadius[part];
        [unroll] for (int d = 0; d < 4; d++)
            if (Toon_LineSilhouettePart(idP, IdAt(p + dirs[d] * r)) > 0.5)
                return part;  // p が手前側（持ち主）
    }
    [unroll] for (int d2 = 0; d2 < 4; d2++)
    {
        [loop] for (int k = 1; k <= gSilMaxRadius; k++)
        {
            int2 q = p + dirs[d2] * k;
            float4 idQ = IdAt(q);
            int qp = SilPart(Toon_LineSilhouettePart(idQ, idP));
            if (qp > 0 && k <= (int)gSilRadius[qp])
            {
                owner = q;  // p は奥側、q が持ち主
                return qp;
            }
        }
    }
    return 0;
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
    if (gSilEnabled)
    {
        int2 owner;
        int part = EdgeSilhouette(p, idP, owner);
        if (part > 0)
        {
            float3 ownerColor = gColorTex.Load(int3(clamp(owner, int2(0, 0), int2(gScreenSize) - 1), 0)).rgb;
            c.rgb = Toon_Tonemap(Toon_OutlineColor(ownerColor, float3(gSilR[part], gSilG[part], gSilB[part]), gSilMix[part]), gTonemap);
        }
    }
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
