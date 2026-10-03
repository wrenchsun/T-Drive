"""FacialController のコア（Maya 非依存・標準ライブラリのみ）。

- model: `.fcpose.json` と 1:1 のデータクラス
- fcpose_io: `.fcpose.json` の読み書き
- evaluate: 角度 → 重みの計算（UE 版 `FacialCore` の写し）
- space: 座標系の変換（Maya / Unity / UE ⇔ 正準空間）
- naming: `FC_*` などシェイプ名の規則（唯一の置き場）
"""
