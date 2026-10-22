"""T-Drive アニメーション作業台（docs/20）。殻（tdrive.shell）の 1 段目タブ「Animation」。

命名 / クリップ / ブレンド / ショット / 出力。dx11Shader・Render Override・.fx は使わない（DirectX 11 でない環境でも動く）。
`core/` は Maya 非依存（pytest）、それ以外は Maya に依存する（mayapy のヘッドレスでスモーク）。
"""
