# T-Drive Toon の Maya モジュールをユーザー環境に登録する。
# %USERPROFILE%\Documents\maya\modules\TDriveToon.mod を生成し、このリポジトリの maya/ を指す。
# 使い方: powershell -ExecutionPolicy Bypass -File tools\install_maya_module.ps1
param(
    [string]$MayaVersion = "2026"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$moduleRoot = (Join-Path $repoRoot "maya").Replace('\', '/')
$modulesDir = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "maya\modules"
New-Item -ItemType Directory -Force -Path $modulesDir | Out-Null

$version = (Get-Content (Join-Path $repoRoot "VERSION") -Raw).Trim()
$modPath = Join-Path $modulesDir "TDriveToon.mod"
$content = @"
+ MAYAVERSION:$MayaVersion TDriveToon $version $moduleRoot
scripts: scripts
TDRIVE_ROOT=$($repoRoot.Replace('\', '/'))
"@
[System.IO.File]::WriteAllText($modPath, $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Installed: $modPath"
Write-Host "  -> $moduleRoot"
Write-Host "Maya $MayaVersion を再起動すると 'T-Drive Toon' メニューと commandPort :7001 が有効になります。"
