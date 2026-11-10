# 文档检查（入库的发布门禁脚本, 由 ci.yml 与 release.yml 调用）
# 用法: pwsh -File scripts/check_docs_style.ps1
# 范围: 只查能客观判定的项(机器相关定位信息 / 排版体例 / 术语 / 交叉引用)。
# 口语词表一类靠语感的规则**刻意不在此处**: 穷举不可能完整, 且会把风格偏好变成必须通过的硬性项。
$root = Split-Path -Parent $PSScriptRoot
$files = @{
  'AGENTS.md' = Join-Path $root 'AGENTS.md'
  'wiki.md'   = Join-Path $root 'wiki.md'
}
$fails = 0
$notes = @()

function Check([string]$title, [string]$file, [string]$pattern, [int]$max, [string]$exempt) {
  $hits = @()
  foreach ($m in Select-String -Path $file -Pattern $pattern -AllMatches -Encoding utf8) {
    foreach ($mm in $m.Matches) {
      if ($exempt -and $m.Line -match $exempt) { continue }
      $hits += "{0}:{1}" -f $m.LineNumber, $m.Line.Trim()
    }
  }
  $n = $hits.Count
  $ok = $n -le $max
  if (-not $ok) { $script:fails++ }
  "{0} {1,-28} 命中={2} (上限 {3})" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $title, $n, $max
  if (-not $ok) { $hits | Select-Object -First 3 | ForEach-Object { "      $_" } }
}

foreach ($name in $files.Keys) {
  $f = $files[$name]
  "===== $name ====="
  # 文件缺失或为空时后续正则无从判定；显式记入备注，避免把“读不到”误报成一批违规
  if (-not (Test-Path -LiteralPath $f) -or (Get-Item -LiteralPath $f).Length -eq 0) {
    "SKIP {0,-28} 文件缺失或为空" -f '文档内容'
    $notes += "$name 缺失或为空，已跳过该文件的检查"
    continue
  }

  # 1. 禁用内容: 机器相关、会过期的定位信息
  Check '绝对路径/盘符'      $f '[A-Za-z]:\\|LOCALAPPDATA'            0 $null
  Check '代码行号引用'        $f '\.py:[0-9]|:[0-9]{2,4}\b'            0 'http|§|版本'
  Check '提交号/长十六进制'    $f '\b[0-9a-f]{7,40}\b'                 0 $null

  # 2. 排版体例（引号规则需排除代码字面量：仅当引号出现在中文行文中才算违规）
  $quoteHits = @()
  foreach ($m in Select-String -Path $f -Pattern '"([^"\n]*)"' -AllMatches -Encoding utf8) {
    foreach ($mm in $m.Matches) {
      if ($mm.Groups[1].Value -notmatch '[\u4e00-\u9fff]') { continue }
      $before = if ($mm.Index -gt 0) { $m.Line[$mm.Index - 1] } else { '' }
      $afterIdx = $mm.Index + $mm.Length
      $after = if ($afterIdx -lt $m.Line.Length) { $m.Line[$afterIdx] } else { '' }
      if ($before -match '[A-Za-z0-9_`]' -or $after -match '[A-Za-z0-9_`]') { continue }
      $quoteHits += "{0}:{1}" -f $m.LineNumber, $m.Line.Trim()
    }
  }
  if ($quoteHits.Count -gt 0) { $fails++ }
  "{0} {1,-28} 命中={2} (上限 0)" -f $(if ($quoteHits.Count -eq 0) { 'PASS' } else { 'FAIL' }), '中文语境仍用直引号', $quoteHits.Count
  $quoteHits | Select-Object -First 3 | ForEach-Object { "      $_" }

  Check '标点连用'            $f '：：|，：|：。|。：|：，|；：|：；|。。|，，' 0 $null
  Check '半角括号含中文'       $f '\(''[^)]*[\u4e00-\u9fff][^)]*\)'    0 '`'   # 代码字面量豁免

  # 3. 术语一致性(只留可判定的固定写法)
  Check '术语: 后端(应为内核)'  $f '后端'                               0 $null

  # 4. 交叉引用完整性
  $refs = @()
  $otherName = if ($name -eq 'AGENTS.md') { 'wiki.md' } else { 'AGENTS.md' }
  $otherPath = Join-Path $root $otherName
  $otherText = if (Test-Path -LiteralPath $otherPath) {
    Get-Content -LiteralPath $otherPath -Raw -Encoding utf8
  } else { '' }

  if ([string]::IsNullOrWhiteSpace($otherText)) {
    # 跨文档校验要求两份文档同时在位；只有一份时如实跳过，不把缺少目标文档报成引用失效
    "SKIP {0,-28} {1} 缺失或为空" -f '交叉引用完整', $otherName
    $notes += "$name -> 交叉引用校验跳过（$otherName 缺失或为空）"
  } else {
    $selfText = Get-Content -LiteralPath $f -Raw -Encoding utf8
    $targets = [regex]::Matches($otherText, '(?m)^#{2,3} [0-9][^\r\n]*') |
      ForEach-Object { (($_.Value -replace '^#+\s*', '') -split '\s+')[0].TrimEnd('.') } | Sort-Object -Unique
    if ($name -eq 'AGENTS.md') {
      $refs = [regex]::Matches($selfText, 'wiki §[0-9]+(?:\.[0-9]+)?') |
        ForEach-Object { $_.Value -replace 'wiki §', '' } | Sort-Object -Unique
    } else {
      $refs = [regex]::Matches($selfText, 'AGENTS\.md`?\s*§[0-9]+(?:\.[0-9]+)?') |
        ForEach-Object { $_.Value -replace '.*§', '' } | Sort-Object -Unique
    }
    $missing = $refs | Where-Object { $targets -notcontains $_.TrimEnd('.') }
    $ok = ($missing.Count -eq 0)
    if (-not $ok) { $fails++ ; foreach ($m in $missing) { $notes += "$name -> 缺失引用 §$m" } }
    "{0} {1,-28} 引用={2} 缺失={3}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), '交叉引用完整', $refs.Count, $missing.Count
  }
}

''
if ($fails -eq 0) { '结论: 全部通过' } else { "结论: $fails 项未通过" }
if ($notes.Count) { '备注:'; $notes | ForEach-Object { "  $_" } }
exit $(if ($fails -eq 0) { 0 } else { 1 })
