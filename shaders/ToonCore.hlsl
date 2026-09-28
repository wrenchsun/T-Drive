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

#endif // TDRIVE_TOON_CORE_INCLUDED
