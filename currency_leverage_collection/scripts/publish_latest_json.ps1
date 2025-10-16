param(
  [string]$RepoDir = (Resolve-Path "$PSScriptRoot\..\..").Path,
  [int]$Keep = 1
)

# Fail on errors
$ErrorActionPreference = "Stop"

Write-Host "RepoDir:" $RepoDir
$JsonDir = Join-Path $RepoDir "currency_leverage_collection\result\html"
if (-not (Test-Path $JsonDir)) {
  Write-Error "JSON directory not found: $JsonDir"
}

$SuggestDir = Join-Path $RepoDir "currency_leverage_collection\result\suggest"

# Find leverage JSON files
$files = Get-ChildItem -Path $JsonDir -Filter "Leverage&Margin_*.json" -File | Sort-Object LastWriteTime
if (-not $files) {
  Write-Host "No leverage JSON found under $JsonDir"
  exit 1
}

# Newest files to keep
$toKeep = $files | Select-Object -Last $Keep
$latest = $toKeep[-1]
Write-Host "Latest JSON:" $latest.FullName

# Files to delete (older ones)
$toDelete = $files | Where-Object { $_.FullName -notin ($toKeep | ForEach-Object FullName) }

# Git sanity
Set-Location $RepoDir

# Remove older JSONs from disk and stage deletions in Git (relative paths)
foreach ($f in $toDelete) {
  try {
    Write-Host "Deleting old JSON:" $f.FullName
    Remove-Item -Force $f.FullName
    $rel = $f.FullName.Replace($RepoDir + "\", "")
    git rm -f --ignore-unmatch -- "$rel" | Out-Null
  } catch {
    Write-Warning "Failed to delete $($f.FullName): $_"
  }
}

# Stage latest JSON (relative path)
$latestRel = $latest.FullName.Replace($RepoDir + "\", "")
git add -- "$latestRel"

# Also handle latest suggest JSON if present
if (Test-Path $SuggestDir) {
  $sfiles = Get-ChildItem -Path $SuggestDir -Filter "suggest_rules_*.json" -File | Sort-Object LastWriteTime
  if ($sfiles) {
    $sKeep = $sfiles | Select-Object -Last $Keep
    $sLatest = $sKeep[-1]
    # Delete older
    $sDelete = $sfiles | Where-Object { $_.FullName -notin ($sKeep | ForEach-Object FullName) }
    foreach ($sf in $sDelete) {
      try {
        Write-Host "Deleting old suggest JSON:" $sf.FullName
        Remove-Item -Force $sf.FullName
        $rel = $sf.FullName.Replace($RepoDir + "\", "")
        git rm -f --ignore-unmatch -- "$rel" | Out-Null
      } catch {
        Write-Warning "Failed to delete $($sf.FullName): $_"
      }
    }
    $sRel = $sLatest.FullName.Replace($RepoDir + "\", "")
    git add -- "$sRel"
  } else {
    Write-Host "No suggest JSON found under $SuggestDir"
  }
}

# Commit if there are staged changes
$diff = git diff --cached --name-only
if ([string]::IsNullOrWhiteSpace($diff)) {
  Write-Host "No staged changes. Nothing to commit."
  exit 0
}

$time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$commitMsg = "data: update leverage JSON ($time)"

# Optional: set user if not configured
try {
  $hasName = (git config user.name) 2>$null
  $hasEmail = (git config user.email) 2>$null
  if (-not $hasName) { git config user.name "auto-bot" | Out-Null }
  if (-not $hasEmail) { git config user.email "auto-bot@example.com" | Out-Null }
} catch { }

# Pull-rebase to reduce conflicts, then push
try {
  git pull --rebase
} catch {
  Write-Warning "git pull --rebase failed: $_"
}

git commit -m $commitMsg

git push

Write-Host "Published latest JSON and pushed to remote."
