// ToonCore.hlsl — T-Drive Toon の式の唯一の実装（docs/03_shader_spec.md）
//
// Unity (MS2026/Toon.shader) と Maya (maya/shaders/TDriveToon.fx, dx11Shader) の両方が #include する。
// 両環境でコンパイルできるための規則（docs/09_render_parity.md D-5）:
//   - テクスチャのサンプリング・行列・頂点入力・uniform を含めない（サンプル済みの値を引数で受け取る純関数だけ）
//   - float 系の型だけを使う（half は使わない）
//   - Unity のマクロや Maya のセマンティクスを書かない
//   - 関数名は必ず Toon_ 接頭辞（パラメータ名 = uniform 名の Toon* と衝突させない）
// 式を変えたら docs/03 を先に更新し、パリティテスト（docs/09 §5）を通すこと。

#ifndef TDRIVE_TOON_CORE_INCLUDED
#define TDRIVE_TOON_CORE_INCLUDED

#define TOON_TONEMAP_NONE    0
#define TOON_TONEMAP_NEUTRAL 1

// ------------------------------------------------------------------ マスク
// 頂点カラーとマスクテクスチャはどちらも「白 = 何もしない」。乗算で合成する（docs/03 §4）
//   R: 黒 → 常に影 / G: 黒 → 線が消える / B: 黒 → 常に明 / A: 黒 → 固定色
float4 Toon_CombineMask(float4 vertexColor, float4 maskMap)
{
    return vertexColor * maskMap;
}

// ------------------------------------------------------------------ 影（docs/03 §2）
// 戻り値: 1 = 明、0 = 影。影の強さ 0 なら常に 1
float Toon_LitFactor(float3 N, float3 L, float4 mask, float threshold, float feather, float strength)
{
    float halfLambert = dot(N, L) * 0.5 + 0.5;
    float x = saturate(halfLambert * mask.r + (1.0 - mask.b));
    float lit = smoothstep(threshold - feather, threshold + feather, x);
    return lerp(1.0, lit, strength);
}

float3 Toon_Shade(float3 baseColor, float lit, float3 shadeColor, float3 lightColor)
{
    return lerp(baseColor * shadeColor, baseColor, lit) * lightColor;
}

float3 Toon_ApplyTint(float3 color, float4 mask, float3 tintColor, float tintStrength)
{
    float t = saturate((1.0 - mask.a) * tintStrength);
    return lerp(color, color * tintColor, t);
}

// ------------------------------------------------------------------ P1（docs/03 §2 / §3.1）
// T-11: ライト色の影響（0 = 受けない）
float3 Toon_LightColor(float3 lightColor, float influence)
{
    return lerp(float3(1.0, 1.0, 1.0), lightColor, influence);
}

// T-13: リム（明側のみ）。戻り値はリム色に掛ける係数
float Toon_Rim(float3 N, float3 V, float power, float strength, float lit)
{
    return pow(1.0 - saturate(dot(N, V)), power) * strength * lit;
}

// T-12: 髪ハイライトの帯をサンプルする UV（カメラの上下の角度で縦にずらす）
float2 Toon_HairHighlightUV(float2 uv, float3 V, float3 up, float shift)
{
    return uv + float2(0.0, shift * dot(V, up));
}

// T-09: 視線方向に沿ってカメラへ寄せる（投影位置は変わらず深度だけ手前になる）。offset はワールド単位
float3 Toon_DepthOffsetWS(float3 positionWS, float3 cameraPosWS, float offset)
{
    return positionWS + normalize(cameraPosWS - positionWS) * offset;
}

// T-18: 線幅の距離補正係数。distance / refDistance は同じ単位（m）
float Toon_OutlineDistanceFactor(float distance, float refDistance, float scale)
{
    float k = saturate(refDistance / max(distance, 1e-4));
    return lerp(1.0, k, scale);
}

// ------------------------------------------------------------------ P2（docs/03 §2 / §3）
// T-27: 2 影を含む影の色。lit2 = 1 のとき 1 影色、0 のとき 2 影色
float Toon_Lit2Factor(float x, float threshold2, float feather, float strength2)
{
    return lerp(1.0, smoothstep(threshold2 - feather, threshold2 + feather, x), strength2);
}

// 本体の 2 階調影の入力 x（Toon_LitFactor と同じ）。2 影の判定に使う
float Toon_ShadeInput(float3 N, float3 L, float4 mask)
{
    float halfLambert = dot(N, L) * 0.5 + 0.5;
    return saturate(halfLambert * mask.r + (1.0 - mask.b));
}

float3 Toon_Shade2(float3 baseColor, float lit, float lit2, float3 shadeColor, float3 shade2Color, float3 lightColor)
{
    float3 shadeC = lerp(baseColor * shade2Color, baseColor * shadeColor, lit2);
    return lerp(shadeC, baseColor, lit) * lightColor;
}

// T-28: 彩度・明るさ（1 = そのまま）
float3 Toon_ColorCorrect(float3 color, float saturation, float brightness)
{
    float luma = dot(color, float3(0.2126, 0.7152, 0.0722));
    return lerp(luma.xxx, color, saturation) * brightness;
}

// T-30: ビュー空間の法線 → MatCap の UV
float2 Toon_MatCapUV(float3 normalVS)
{
    return normalVS.xy * 0.5 + 0.5;
}

// T-21: SDF 顔影マップ（docs/03 §2.1）。F / R は顔の正面・右（ワールド）、L は表面 → 光源
float Toon_FaceLightAngle01(float3 F, float3 L)
{
    float2 f = normalize(F.xz + float2(1e-6, 0.0));
    float2 l = normalize(L.xz + float2(1e-6, 0.0));
    return acos(clamp(dot(f, l), -1.0, 1.0)) / 3.14159265;
}

float2 Toon_FaceShadowUV(float2 uv, float3 R, float3 L)
{
    return dot(R.xz, L.xz) >= 0.0 ? uv : float2(1.0 - uv.x, uv.y);  // マップは「右から照らす」向きで作る
}

// value = マップの R（明るいままでいられる最大角度）。戻り値は影の強さ・効かせ具合を合成した lit
float Toon_FaceShadowLit(float lit, float value, float angle01, float feather, float shadowStrength, float weight)
{
    float litF = smoothstep(angle01 - feather, angle01 + feather, value);
    return lerp(lit, lerp(1.0, litF, shadowStrength), weight);
}

// T-22: ビュー空間で中心の奥行きへ寄せる（xy はそのまま = 遠近感が弱まる）。k = 量 × 効かせ具合
float3 Toon_DepthCompressVS(float3 positionVS, float3 pivotVS, float k)
{
    positionVS.z = lerp(positionVS.z, pivotVS.z, saturate(k));
    return positionVS;
}

// T-26: 線の太さの方向依存（影側・下向きの面を太く）
float Toon_OutlineDirectionFactor(float3 normalWS, float3 L, float shadowSide, float bottom)
{
    float litV = saturate(dot(normalWS, L) * 0.5 + 0.5);
    float f = lerp(shadowSide, 1.0, litV);
    return f * lerp(1.0, bottom, saturate(-normalWS.y));
}

// ------------------------------------------------------------------ アウトライン（docs/03 §3）
// widthPx は「1080p 換算の px」= 画面高さの 1/1080 単位。クリップ空間で押し出すため距離によらず一定。
// 方向はピクセル空間で正規化する（NDC のまま正規化すると横向きの輪郭で線が 幅/高さ 倍に太る）
float2 Toon_OutlineClipOffset(float2 normalClipXY, float widthPx, float4 mask, float clipW, float2 screenSize)
{
    float2 halfScreen = screenSize * 0.5;
    float2 dirPx = normalClipXY * halfScreen;
    float len = length(dirPx);
    dirPx = len > 1e-5 ? dirPx / len : float2(0.0, 0.0);
    float px = widthPx * mask.g * screenSize.y / 1080.0;
    return dirPx * px / halfScreen * clipW;
}

float3 Toon_OutlineColor(float3 baseColor, float3 outlineColor, float baseMix)
{
    return lerp(outlineColor, outlineColor * baseColor, baseMix);
}

// ------------------------------------------------------------------ スムーズ法線（docs/03 §5）
// UV2 に八面体エンコードで格納した接空間スムーズ法線を復元する
float3 Toon_OctahedralDecode(float2 e)
{
    float3 n = float3(e.x, e.y, 1.0 - abs(e.x) - abs(e.y));
    float t = saturate(-n.z);
    n.x += n.x >= 0.0 ? -t : t;
    n.y += n.y >= 0.0 ? -t : t;
    return normalize(n);
}

// normalWS / tangentWS は正規化済み。tangentSign は tangent.w（UV の裏返り）
float3 Toon_SmoothNormalWS(float2 encoded, float3 normalWS, float3 tangentWS, float tangentSign)
{
    float3 bitangentWS = cross(normalWS, tangentWS) * tangentSign;
    float3 nTS = Toon_OctahedralDecode(encoded);
    return normalize(tangentWS * nTS.x + bitangentWS * nTS.y + normalWS * nTS.z);
}

// ------------------------------------------------------------------ トーンマップ
// Unity ではポストプロセスが掛けるので Unity 側シェーダーは呼ばない。Maya プレビューの最終段でだけ使う。
// Neutral: URP (Core RP Color.hlsl) の NeutralTonemap と同じ John Hable のカーブと定数
float3 Toon_NeutralCurve(float3 x, float a, float b, float c, float d, float e, float f)
{
    return ((x * (a * x + c * b) + d * e) / (x * (a * x + b) + d * f)) - e / f;
}

float3 Toon_TonemapNeutral(float3 x)
{
    x = max(float3(0.0, 0.0, 0.0), x);
    const float a = 0.2;
    const float b = 0.29;
    const float c = 0.24;
    const float d = 0.272;
    const float e = 0.02;
    const float f = 0.3;
    const float whiteLevel = 5.3;
    const float whiteClip = 1.0;
    float3 whiteScale = 1.0 / Toon_NeutralCurve(float3(whiteLevel, whiteLevel, whiteLevel), a, b, c, d, e, f);
    x = Toon_NeutralCurve(x * whiteScale, a, b, c, d, e, f);
    x *= whiteScale;
    return x / whiteClip;
}

float3 Toon_Tonemap(float3 x, int mode)
{
    if (mode == TOON_TONEMAP_NEUTRAL)
        return Toon_TonemapNeutral(x);
    return x;
}

// T-29 接地影（docs/08 §3.4）: 足の地面上の水平距離 d・地面からの高さ h（radius と同じ単位）→ 地面を暗くする割合 0..1。
// 足ごとに求めて max で合成し、地面の色に (1 - 割合) を掛ける。
float Toon_ContactShadow(float d, float h, float radius, float strength)
{
    float r = max(radius, 1e-5);
    return strength * (1.0 - smoothstep(0.0, r, d)) * saturate(1.0 - max(h, 0.0) / r);
}

// ------------------------------------------------------------------ セルフシャドウ（P2 T-43、docs/03 §2.3）
// atten: シャドウマップの比較（1 = 光が当たる、0 = 影）。mask.b 黒（常に明）の所には落ちない
float Toon_SelfShadowLit(float lit, float atten, float4 mask, float feather, float strength, float receive)
{
    float s = smoothstep(0.5 - feather, 0.5 + feather, atten);
    return min(lit, lerp(1.0, s, strength * receive * mask.b));
}

// ------------------------------------------------------------------ 法線マップ・発光（Common、docs/03 §2.2）
// 法線マップ: Unity の UnpackNormalmapRGorAG + UnpackNormalScale と同じ（+Y = OpenGL 形式、テクスチャはリニア）
float3 Toon_UnpackNormal(float4 packed, float scale)
{
    float2 xy = float2(packed.r * packed.a, packed.g) * 2.0 - 1.0;
    xy *= scale;
    return float3(xy, sqrt(saturate(1.0 - dot(xy, xy))));
}

// normalWS / tangentWS は正規化済み、tangentSign = tangent.w（UV の裏返り）
float3 Toon_NormalFromMap(float4 packed, float scale, float3 normalWS, float3 tangentWS, float tangentSign)
{
    float3 nTS = Toon_UnpackNormal(packed, scale);
    float3 bitangentWS = cross(normalWS, tangentWS) * tangentSign;
    return normalize(tangentWS * nTS.x + bitangentWS * nTS.y + normalWS * nTS.z);
}

// 発光: 影・ライト色・色補正の影響を受けない。トーンマップの前に足す。emissionMap は sRGB → リニア済み、color もリニア
float3 Toon_Emission(float3 color, float3 emissionMap, float3 emissionColor, float intensity)
{
    return color + emissionMap * emissionColor * intensity;
}

// ------------------------------------------------------------------ 画面上の線（T-23 / T-42、docs/03 §10）
#define TOON_LINE_CREASE_DEG 60.0
#define TOON_LINE_DEPTH_REL 0.03

float2 Toon_OctahedralEncode(float3 n)
{
    n /= (abs(n.x) + abs(n.y) + abs(n.z));
    float2 e = n.xy;
    if (n.z < 0.0)
        e = (1.0 - abs(e.yx)) * float2(e.x >= 0.0 ? 1.0 : -1.0, e.y >= 0.0 ? 1.0 : -1.0);
    return e;
}

// ToonId バッファに書く値。normalVS は正規化済み、depthM はビュー空間の奥行き（m）、partKey = 部位番号 × 2 + 線フラグ。
// 背面押し出しの輪郭線のパスは isOutline = true（A を −奥行きにして印を付ける。外形には含め、内側の線の判定からは外す）
float4 Toon_LineIdValue(float3 normalVS, float depthM, float partKey, bool isOutline)
{
    return float4(Toon_OctahedralEncode(normalVS), partKey, isOutline ? -depthM : depthM);
}

// 線を出す間隔 r（画素）。widthPx は px@1080p、screenHeight は実際の画面の高さ
float Toon_LineRadius(float widthPx, float screenHeight)
{
    return max(1.0, round(widthPx * screenHeight / 1080.0 * 0.5));
}

// p と q（ToonId の値）の間に内側の線があるか（0 / 1）
float Toon_LineInnerPair(float4 p, float4 q)
{
    if (p.b < 1.5 || q.b < 1.5)
        return 0.0;  // どちらかが Toon でない（外側輪郭は Toon_LineOuterPair）
    if (p.a < 0.0 || q.a < 0.0)
        return 0.0;  // 背面押し出しの輪郭線の画素（A = −奥行き）は内側の線の判定に使わない
    float partP = floor(p.b * 0.5 + 0.25), partQ = floor(q.b * 0.5 + 0.25);
    float lineP = p.b - partP * 2.0, lineQ = q.b - partQ * 2.0;
    if (partP != partQ)
        return (lineP > 0.5 || lineQ > 0.5) ? 1.0 : 0.0;
    if (lineP < 0.5)
        return 0.0;
    float3 nP = Toon_OctahedralDecode(p.rg), nQ = Toon_OctahedralDecode(q.rg);
    if (dot(nP, nQ) < cos(radians(TOON_LINE_CREASE_DEG)))
        return 1.0;
    return abs(p.a - q.a) > min(p.a, q.a) * TOON_LINE_DEPTH_REL ? 1.0 : 0.0;
}

// 透かし線（T-44、docs/03 §10.5）: op / oq = 透かしバッファ（透かしの部位だけを描いた ToonId）、mainP = 通常の ToonId（p の位置）。
// p が透かしの部位の外形で、p で見えているのが別の面、しかもその面が手前（距離 maxDist 以内）のとき 1
// occluderMask: 線を出す手前の部位（ビット = 部位番号 - 1。部位番号 = floor(B / 2)）。顔の皮膚が目の縁を覆う所などには出さない
float Toon_LineSeeThroughPair(float4 op, float4 oq, float4 mainP, float maxDist, uint occluderMask)
{
    if (op.b < 1.5 || abs(op.b - oq.b) < 0.5)
        return 0.0;  // p が透かしの部位でない / 外形でない
    if (mainP.b < 1.5 || abs(mainP.b - op.b) < 0.5)
        return 0.0;  // p でその部位自身が見えている（重なっていない）→ 通常の描画
    uint occ = (uint)floor(mainP.b * 0.5) - 1u;
    if (occ > 31u || ((occluderMask >> occ) & 1u) == 0u)
        return 0.0;  // 手前にあるのが透かす部位（髪など）でない
    float front = abs(mainP.a);  // 輪郭線の画素は A = −奥行き（§10.1）
    float gap = op.a - front;
    return (gap > 0.0 && gap <= maxDist) ? 1.0 : 0.0;
}

// p と q の片方だけが Toon のとき外側輪郭（T-42）
float Toon_LineOuterPair(float4 p, float4 q)
{
    return ((p.b > 1.5) != (q.b > 1.5)) ? 1.0 : 0.0;
}

#endif // TDRIVE_TOON_CORE_INCLUDED
