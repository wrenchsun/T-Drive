# 外部ツールの導入（docs/13 §6.3）。install.ps1 の -Tools から呼ばれる（dot-source して Add-ExternalTools を使う）。
#   書式: "<url>[#tag],<url>[#tag],..."（tag は vX.Y.Z。省略は最新のリリースタグ）
#   各ツール: tools\_adding_<乱数> に clone（LFS なし）→ tool.json の id → タグに checkout → tools\<id> に改名 → tools.json に追記
#   既に同じ id がある・tools\<id> がある・tool.json が無い・clone できない、のときは何も残さず次へ（本体の導入は止めない）
# 基点（TDriveToon フォルダ）は -Base（省略で %LOCALAPPDATA%\TDriveToon）。tools.json の形は maya/scripts/tdrive/external.py と同じ。

if (-not (Get-Command Say -ErrorAction SilentlyContinue)) {
    function Say([string]$msg, [string]$color = "") {
        if ($color) { Write-Host $msg -ForegroundColor $color } else { Write-Host $msg }
    }
}
if (-not (Get-Command Parse-Version -ErrorAction SilentlyContinue)) {
    function Parse-Version([string]$tag) {
        if ($tag -match '^v(\d+)\.(\d+)\.(\d+)$') { return [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])" }
        return $null
    }
}

function Invoke-ToolGit([string]$dir) {
    # git の進捗は stderr に出るので、Stop のままだと例外になる。終了コードだけで判定する
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($dir) { $out = & git -C $dir @args 2>&1 } else { $out = & git @args 2>&1 }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    if ($code -ne 0) { throw "git $($args -join ' '): $(($out | ForEach-Object { "$_" }) -join ' ')" }
    return $out
}

function Read-ToolsJson([string]$path) {
    if (-not (Test-Path $path)) { return @() }
    $text = [System.IO.File]::ReadAllText($path, (New-Object System.Text.UTF8Encoding($false)))
    if (-not $text.Trim()) { return @() }
    $data = $text | ConvertFrom-Json
    if ($data.tools) { return @($data.tools) }
    return @()
}

function Write-ToolsJson([string]$path, $tools) {
    $doc = [ordered]@{ version = 1; tools = @($tools) }
    $json = ConvertTo-Json -InputObject $doc -Depth 5
    [System.IO.File]::WriteAllText($path, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
}

function Add-ExternalTool([string]$url, [string]$tag, [string]$base) {
    $toolsDir = Join-Path $base "tools"
    $jsonPath = Join-Path $base "tools.json"
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $temp = Join-Path $toolsDir ("_adding_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        Invoke-ToolGit "" clone --quiet $url $temp | Out-Null
        $manifestPath = Join-Path $temp "tool.json"
        if (-not (Test-Path $manifestPath)) { Say "注意: $url に tool.json がありません。外部ツールとして使えないので追加しません" "Yellow"; return }
        $manifest = [System.IO.File]::ReadAllText($manifestPath, (New-Object System.Text.UTF8Encoding($false))) | ConvertFrom-Json
        $id = "$($manifest.id)"
        if ($id -notmatch '^[a-z][a-z0-9_]*$') { Say "注意: $url の tool.json の id が正しくありません（$id）。追加しません" "Yellow"; return }
        $existing = @(Read-ToolsJson $jsonPath)
        $final = Join-Path $toolsDir $id
        if (($existing | Where-Object { $_.id -eq $id }) -or (Test-Path $final)) { Say "外部ツール $id は既にあります（そのままにします）"; return }
        if ($tag) {
            if (-not (Parse-Version $tag)) { Say "注意: タグは vX.Y.Z の形で指定してください（$tag）。$url は追加しません" "Yellow"; return }
        } else {
            $tags = @(Invoke-ToolGit $temp tag --list "v*") | ForEach-Object { "$_".Trim() } | Where-Object { Parse-Version $_ }
            if ($tags) { $tag = $tags | Sort-Object { Parse-Version $_ } -Descending | Select-Object -First 1 }
        }
        if ($tag) { Invoke-ToolGit $temp -c advice.detachedHead=false checkout --quiet $tag | Out-Null }
        Move-Item -LiteralPath $temp -Destination $final
        $entry = [ordered]@{ id = $id; url = $url; dir = $id; tag = "$tag"; enabled = $true }
        Write-ToolsJson $jsonPath (@($existing) + @($entry))
        $shown = if ($tag) { $tag } else { "既定のブランチ" }
        Say "外部ツール: $id（$shown）を導入しました"
    } catch {
        Say "注意: $url を外部ツールとして取り込めませんでした: $_" "Yellow"
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Add-ExternalTools([string]$spec, [string]$base = "") {
    if (-not $spec) { return }
    if (-not $base) { $base = Join-Path $env:LOCALAPPDATA "TDriveToon" }
    foreach ($item in ($spec -split ',')) {
        $item = $item.Trim()
        if (-not $item) { continue }
        $url = $item; $tag = ""
        $i = $item.LastIndexOf('#')
        if ($i -ge 0) { $url = $item.Substring(0, $i).Trim(); $tag = $item.Substring($i + 1).Trim() }
        if (-not $url) { continue }
        Add-ExternalTool $url $tag $base
    }
}
