# 02. Look 定義仕様（schemaVersion 1）

`looks/<character>/look.json` — 1 キャラクター分のルックの唯一の真実。
構造は **D-Drive MaterialData と 1 対 1** に揃える（Unity 側で機械的に MaterialData を生成できること）。

## 1. 全体構造

```jsonc
{
  "schemaVersion": 1,                 // この仕様のバージョン（整数。破壊的変更で +1）
  "character": "unitychan",           // キャラクター ID（英数小文字・_ のみ）
  "model": "assets/unitychan/unitychan.fbx",  // リポジトリ相対
  "lookVersion": "0.1.0",             // このキャラクターのルックの版（SemVer, 06 §4）
  "parts":     { "<partName>": Part },
  "materials": { "<materialName>": Material },   // base 値
  "variants":  { "<variantName>": Variant }      // A/B 比較用の差分
}
```

- キー順はソート固定・インデント 2・末尾改行（差分レビュー用。`look.dumps()` が保証）
- パスはすべてリポジトリ相対の `/` 区切り

## 2. Part（部位）

```jsonc
"face": { "role": "face", "materials": ["face", "mouth"] }
```

| フィールド | 型 | 説明 |
|---|---|---|
| role | string | ロール ID（§5）。プリセットと描画順の既定を決める |
| materials | string[] | Maya のマテリアル名（= Unity の元マテリアル名）。**1 マテリアルは 1 部位にのみ所属** |

- 部位の単位は **マテリアル**。Unity のマテリアルスロット（D-Drive `ModelData.Slots`）と一致させるため
- 1 マテリアル内で部位を分けたい場合（例: 顔テクスチャ内の眉）は、モデル側でマテリアルを分けるか、Toon マスクで表現する

## 3. Material（マテリアル）

```jsonc
"face": {
  "shader": "MS2026/Toon",
  "common": {                          // → MaterialData.Common（名前は camelCase 化）
    "albedo": "assets/unitychan/textures/face_00.tga",
    "albedoTint": [1, 1, 1, 1],
    "normal": null, "normalScale": 1.0,
    "emission": null, "emissionColor": [0, 0, 0, 1], "emissionIntensity": 0.0,
    "blend": "Opaque",                 // Opaque | Cutout | Transparent
    "cutoff": 0.5,
    "doubleSided": false
  },
  "specific": {                        // → MaterialData.Specific[]（キー = Unity プロパティ名）
    "_ToonShadeColor": [0.78, 0.72, 0.86, 1.0],
    "_ToonShadowStrength": 0.35
  },
  "renderQueueOffset": 0               // → MaterialData.RenderQueueOffset
}
```

- `common` のフィールドは D-Drive `MaterialCommon` に存在するものだけ。Mask チャンネル（Metallic/AO/Smoothness）は PBR 用のため **セルルックでは使わない**（将来 G=AO を影バイアスに流用する余地だけ残す）
- `specific` のキーは **パラメータ契約（[03_shader_spec.md](03_shader_spec.md) §3）に存在するものだけ**。未知のキーは検証エラー
- テクスチャ値は文字列（パス）か `null`

## 4. Variant（バリアント）

```jsonc
"variants": {
  "B": {
    "label": "顔の影を弱く",
    "overrides": {
      "face": { "specific": { "_ToonShadowStrength": 0.1 } }
    }
  }
}
```

- base に対する **疎な上書き**。記載のない値は base を継承
- `base` は予約語（バリアント名に使えない）
- 解決規則: `resolve(v) = base に overrides[v] を common/specific はキー単位でマージ、renderQueueOffset は置換`
- 「採用 (promote)」: バリアントの解決結果を base に確定し、バリアントを削除

## 5. ロール

| ロール | 表示名 | 既定 RQ オフセット | 既定 Blend | 主なプリセット |
|---|---|---|---|---|
| face | 顔 | 0 | Opaque | 影の強さ 0.35 / 線幅 0.6 |
| skin | 肌（体） | 0 | Opaque | 肌向け影色（赤寄り）/ 線幅 0.9 |
| hair | 髪 | 10 | Opaque | 線幅 1.3 / リム 0.25 |
| eye | 目 | 20 | Transparent | 影なし・線なし |
| eyeline | アイライン・まつ毛 | 25 | Transparent | 影なし・線なし |
| brow | 眉 | 30 | Opaque | 影なし・線なし |
| mouth | 口 | 0 | Opaque | 影 0.2 / 線幅 0.4 |
| blush | 頬・紅潮オーバーレイ | 40 | Transparent | 影なし・線なし |
| cloth | 服 | 0 | Opaque | 線幅 1.0 |
| accessory | 装飾 | 0 | Opaque | 線幅 0.8 |
| other | その他 | 0 | Opaque | なし |

- RQ オフセットは「後に描くほど上に出せる」ための予約値。眉・目の髪越し表示（[04](04_technique_priority.md) T-10）で使用
- プリセットは **登録時に新しく部位に入ったマテリアルの base 値へ 1 回だけ適用**。以後の調整値は上書きしない
- マテリアル名からのロール自動推定ルールは `roles.AUTO_RULES`（正規表現、上から優先）

## 6. 検証ルール

`look.validate()` が以下をエラーにする（保存時・読込時に必須）:

1. schemaVersion が 1 以外 / character・model・lookVersion が空
2. 部位のロールが未知 / 部位が未定義マテリアルを参照 / 1 マテリアルが複数部位に所属
3. common に MaterialCommon に無いフィールド / blend が 3 種以外
4. specific にパラメータ契約に無いキー / 型不一致（float・[r,g,b,a]・テクスチャパス|null）
5. renderQueueOffset が整数でない
6. バリアント名 `base` / バリアントが未定義マテリアルを上書き

## 7. Unity 向け中間ファイル

`build/unity/<character>/<character>_<variant>.materialdata.json`（Git 管理外。リリース時に生成）

```jsonc
{
  "schemaVersion": 1, "character": "unitychan", "lookVersion": "0.1.0", "variant": "base",
  "parts": { ... },
  "materials": {
    "face": {
      "Shader": "MS2026/Toon",
      "Common": { "Albedo": "assets/...", "AlbedoTint": [...], "Blend": "Opaque", ... },
      "Specific": [ { "Property": "_ToonShadowStrength", "Value": { "Type": "Float", "FloatValue": 0.35 } } ],
      "RenderQueueOffset": 0,
      "RenderQueue": 2000,
      "SourceMaterial": "Maya/unitychan/face"
    }
  }
}
```

- `Specific[].Value` は D-Drive `ParamValue` と同じ Type 名（Float / Color / Bool / Object）。Object はパス文字列 `ObjectPath` で渡し、インポーターが TextureData に解決する
- `RenderQueue` は確認用（D-Drive 側は Blend と Offset から再計算する）

## 8. 互換性

| 変更 | 区分 |
|---|---|
| フィールド・ロール・パラメータの追加（既定値あり） | MINOR |
| フィールド・キーの削除/改名、意味の変更、解決規則の変更 | MAJOR（schemaVersion +1 とマイグレーション関数を同時に用意） |
