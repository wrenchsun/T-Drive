# T-Drive Toon（Maya ツール）のインストーラー / アップデーター（docs/13 §2）。
#
# 配布用の tools/distribution/Install-TDriveToon.bat から -Release 付きで呼ばれる。やること:
#   1. 前提の確認（git・Maya 2026）
#   2. -Release のとき: 最新のリリースタグ vX.Y.Z（-Version で指定可）に固定。Git LFS の大きなファイルは取らない
#   3. プロジェクトフォルダを決める（-Project > -ProjectHint が Maya のプロジェクトなら > Maya で最後に開いたプロジェクト > 選択画面）
#   4. Maya のモジュール（TDriveToon.mod）を登録。既存のものは .bak に残す
#
# 開発用（このリポジトリを直接使う）は -Release を付けない: 版の固定はせず、今のチェックアウトを登録する。
param(
    [switch]$Release,
    [string]$Version = "",
    [string]$Project = "",
    [string]$ProjectHint = "",
    [string]$ModulesDir = "",
    [string]$MayaVersion = "2026",
    [switch]$EnableMcp,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:GIT_LFS_SKIP_SMUDGE = "1"
$env:GIT_TERMINAL_PROMPT = "0"

function Say([string]$msg, [string]$color = "") {
    if ($color) { Write-Host $msg -ForegroundColor $color } else { Write-Host $msg }
}
function Fail([string]$msg) {
    Write-Host ""
    Write-Host "導入できませんでした: $msg" -ForegroundColor Red
    if (-not $NoPause) { Read-Host "Enter キーで閉じます" | Out-Null }
    exit 1
}
function Invoke-Git {
    $out = & git -C $repoRoot @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' '): $out" }
    return $out
}
function Parse-Version([string]$tag) {
    if ($tag -match '^v(\d+)\.(\d+)\.(\d+)$') { return [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])" }
    return $null
}

Say "=== T-Drive Toon の導入 ==="

# ---- 1. 前提
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git が見つかりません。Git for Windows を入れてください（https://git-scm.com/）" }
$mayaExe = "C:\Program Files\Autodesk\Maya$MayaVersion\bin\maya.exe"
if (-not (Test-Path $mayaExe)) { Say "注意: Maya $MayaVersion が標準の場所に見つかりません（$mayaExe）。別の場所に入っていれば問題ありません" }

# ---- 2. 版の固定
if ($Release) {
    try {
        $changes = Invoke-Git status --porcelain --untracked-files=no
        if ($changes) { Fail "ツール本体（$repoRoot）が変更されています。フォルダを消して入れ直してください:`n$changes" }
        Say "最新の版を確認しています…"
        Invoke-Git fetch --tags --force origin | Out-Null
        $tags = @(Invoke-Git tag --list "v*") | ForEach-Object { "$_".Trim() } | Where-Object { Parse-Version $_ }
        if (-not $tags) { Fail "リリース（タグ vX.Y.Z）がまだありません" }
        if ($Version) {
            $target = if ($Version.StartsWith("v")) { $Version } else { "v$Version" }
            if ($tags -notcontains $target) { Fail "版 $target はありません（ある版: $($tags -join ', ')）" }
        } else {
            $target = $tags | Sort-Object { Parse-Version $_ } -Descending | Select-Object -First 1
        }
        $current = $null
        try { $current = "$(Invoke-Git describe --tags --exact-match HEAD)".Trim() } catch { $current = $null }
        if ($current -eq $target) { Say "版: $target（最新です）" }
        else {
            Invoke-Git -c advice.detachedHead=false checkout --quiet $target | Out-Null
            if (-not $current) { Say "版: $target を導入しました" }
            elseif ((Parse-Version $target) -lt (Parse-Version $current)) { Say "版: $current → $target に戻しました" }
            else { Say "版: $current → $target に更新しました" }
        }
        # マニュアルの画像だけ取る（Git LFS がある場合）
        if (Get-Command git-lfs -ErrorAction SilentlyContinue) {
            try { Invoke-Git lfs pull --include "docs/DesignerManual/images/*" | Out-Null }
            catch { Say "注意: マニュアルの画像を取れませんでした（ツールは使えます）" }
        }
    } catch { Fail "$_" }
}
$toolVersion = (Get-Content (Join-Path $repoRoot "VERSION") -Raw).Trim()

# ---- 3. プロジェクトフォルダ
function Test-ProjectFolder([string]$dir) {
    return [bool]($dir -and (Test-Path $dir) -and ((Test-Path (Join-Path $dir "workspace.mel")) -or (Test-Path (Join-Path $dir "looks"))))
}
function Get-MayaLastProject {
    $docs = [Environment]::GetFolderPath("MyDocuments")
    $prefs = Get-ChildItem -Path (Join-Path $docs "maya\$MayaVersion") -Filter userPrefs.mel -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    foreach ($p in $prefs) {
        $bytes = [System.IO.File]::ReadAllBytes($p.FullName)
        try { $text = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes) }
        catch { $text = [System.Text.Encoding]::Default.GetString($bytes) }  # 日本語版の Maya はシステムの文字コード（cp932）で保存する
        if ($text -match '-sv "lastLocalWS" "([^"]+)"') {
            $ws = $Matches[1].TrimEnd("/")
            if ((Split-Path $ws -Leaf) -ne "default" -and (Test-Path $ws)) { return $ws }  # Maya の既定プロジェクトは使わない
        }
    }
    return $null
}
function Select-Folder([string]$start) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "T-Drive Toon のプロジェクトフォルダ（Look・出力を置く場所）を選んでください"
    if ($start -and (Test-Path $start)) { $dlg.SelectedPath = $start }
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dlg.SelectedPath }
    return $null
}

$how = ""
if ($Project) { $how = "指定" }
elseif (Test-ProjectFolder $ProjectHint) { $Project = $ProjectHint; $how = "インストーラーを置いたフォルダ" }
else {
    $last = Get-MayaLastProject
    if ($last) { $Project = $last; $how = "Maya で最後に開いたプロジェクト" }
    elseif ($Release -and -not $NoPause) { $Project = Select-Folder $ProjectHint; $how = "選択" }
}
if ($Project) {
    $Project = (Resolve-Path $Project).Path
    New-Item -ItemType Directory -Force -Path (Join-Path $Project "looks") | Out-Null
    Say "プロジェクト: $Project（$how）"
} elseif ($Release) {
    Say "注意: プロジェクトを決めていません。Maya の T-Drive Toon › プロジェクトを選ぶ… で選んでください"
}

# ---- 4. Maya モジュールの登録
if (-not $ModulesDir) { $ModulesDir = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "maya\modules" }
New-Item -ItemType Directory -Force -Path $ModulesDir | Out-Null
$modPath = Join-Path $ModulesDir "TDriveToon.mod"
if (Test-Path $modPath) { Copy-Item $modPath "$modPath.bak" -Force }
$root = $repoRoot -replace '\\', '/'
$lines = @("+ MAYAVERSION:$MayaVersion TDriveToon $toolVersion $root/maya", "scripts: scripts", "TDRIVE_ROOT=$root")
if ($Project) {
    $p = $Project -replace '\\', '/'
    $lines += "TDRIVE_PROJECT=$p"
    $lines += "TDRIVE_INSTALL_PROJECT=$p"  # Maya が起動時に「インストーラーで決めたプロジェクト」として採用する
}
if (-not $EnableMcp) { $lines += "TDRIVE_MCP_PORT=0" }  # AI 連携用のポートはデザイナーの PC では開かない
[System.IO.File]::WriteAllText($modPath, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))

Say ""
Say "導入しました: T-Drive Toon $toolVersion" "Green"
Say "  ツール本体: $repoRoot"
Say "  モジュール: $modPath"
Say "Maya $MayaVersion を起動（起動中なら再起動）すると、メニューに「T-Drive Toon」が出ます。"
Say "更新は Maya の T-Drive Toon › 更新… から。このインストーラーをもう一度実行しても最新になります。"
if (-not $NoPause) { Read-Host "Enter キーで閉じます" | Out-Null }
