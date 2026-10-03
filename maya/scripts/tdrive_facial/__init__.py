"""FacialController（カメラ角度に応じた顔の補正）。仕様は docs/14、設計は docs/15。

- `tdrive_facial.core` は Maya 非依存（`maya` を import しない。tests/facial が検査する）
- Maya に触る層（scene / bake / preview_rig / ui_*）は F1 以降で足す
"""
