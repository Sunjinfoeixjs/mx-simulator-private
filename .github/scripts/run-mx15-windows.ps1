#Requires -Version 5.1
<#
MX15 Windows runner launch, fail closed.  Secrets, client binaries and raw
authentication traces must never be committed or uploaded.
For an actual CI job use a private, isolated, trusted Windows runner VM.
Local machine check: .\run-mx15-windows.ps1 -RepoRoot ... -ValidateOnly
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-Condition([bool]$Condition, [string]$Reason) {
    if (-not $Condition) { throw "MX15_RUNNER_BLOCKED: $Reason" }
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$Workspace = Split-Path -Parent $RepoRoot
$Python = (Get-Command python -ErrorAction Stop).Source
$Git = (Get-Command git -ErrorAction Stop).Source
$ClientSource = [string]$env:MX_CI_CLIENT_SRC
$P67Source = [string]$env:MX_CI_P67_REPO

Assert-Condition (Test-Path -LiteralPath (Join-Path $RepoRoot 'docs\mx15\mx15_quality_gates.py')) 'MX15 quality gate missing'
Assert-Condition (Test-Path -LiteralPath (Join-Path $RepoRoot '.git')) 'Not a Git checkout'
Assert-Condition (-not [string]::IsNullOrWhiteSpace($ClientSource)) 'MX_CI_CLIENT_SRC must point to a TRUSTED external client snapshot'
Assert-Condition (-not [string]::IsNullOrWhiteSpace($P67Source)) 'MX_CI_P67_REPO must point to a TRUSTED local P67 Git repository'
Assert-Condition (Test-Path -LiteralPath (Join-Path $ClientSource 'net.py')) 'External client snapshot has no net.py'
Assert-Condition (Test-Path -LiteralPath $P67Source) 'External frozen P67 repository is missing'

$Head = (& $Git -C $P67Source rev-parse --short HEAD)
Assert-Condition ($LASTEXITCODE -eq 0 -and $Head.Trim() -eq '7db4df1') 'P67 trusted snapshot must be at frozen 7db4df1'
$P0 = (& $Git -C $RepoRoot rev-parse 'p0-accepted^{commit}')
Assert-Condition ($LASTEXITCODE -eq 0 -and $P0.Trim().StartsWith('15dd282')) 'P0 tag is missing or has moved'
Write-Host "MX15 preflight: trusted P67=$($Head.Trim()), P0=$($P0.Trim().Substring(0,7)), Python=$Python"

if ($ValidateOnly) {
    Write-Host 'MX15 preflight PASS: no client data copied, no runner configured, no CI jobs executed.'
    exit 0
}

Assert-Condition ($env:GITHUB_ACTIONS -eq 'true') 'Refusing to execute outside GitHub Actions. Use -ValidateOnly for local checks'
Assert-Condition ($env:MX_CI_REPO_PRIVATE -eq 'true') 'Repository must be private'
Assert-Condition ($env:GITHUB_EVENT_NAME -in @('pull_request','push')) 'Event must be pull_request or push'
Assert-Condition ($env:GITHUB_WORKSPACE -and ((Resolve-Path $env:GITHUB_WORKSPACE).Path -eq $Workspace)) 'Unexpected workspace path'
Assert-Condition ($env:RUNNER_OS -eq 'Windows') 'Runner OS must be Windows'

# Never let a PR-supplied path designate our trusted evidence/client source.
$workspacePrefix = $Workspace.TrimEnd('\') + '\'
$clientResolved = (Resolve-Path $ClientSource).Path
$p67Resolved = (Resolve-Path $P67Source).Path
Assert-Condition (-not $clientResolved.StartsWith($workspacePrefix,[StringComparison]::OrdinalIgnoreCase)) 'Trusted client source cannot live inside the PR workspace'
Assert-Condition (-not $p67Resolved.StartsWith($workspacePrefix,[StringComparison]::OrdinalIgnoreCase)) 'Trusted P67 source cannot live inside the PR workspace'

$P67Checkout = Join-Path $Workspace 'MX-p67'
$ClientDest = Join-Path $RepoRoot '客户端源码\src'
Assert-Condition (-not (Test-Path -LiteralPath $P67Checkout)) 'P67 checkout already exists; refusing stale evidence'
Assert-Condition (-not (Test-Path -LiteralPath $ClientDest)) 'Client destination already exists; refusing stale client files'

Write-Host 'Creating disposable independent P67 clone in isolated job workspace'
& $Git clone --local --no-hardlinks --no-checkout -- $P67Source $P67Checkout
Assert-Condition ($LASTEXITCODE -eq 0) 'P67 cloning failed'
& $Git -C $P67Checkout checkout --detach --quiet 7db4df1
Assert-Condition ($LASTEXITCODE -eq 0) 'P67 fixed commit checkout failed'

Write-Host 'Copying only approved client Python source (no ini, account data or binaries)'
New-Item -ItemType Directory -Force -Path $ClientDest | Out-Null
$clientFiles = @(Get-ChildItem -LiteralPath $ClientSource -Recurse -File -Filter '*.py')
Assert-Condition ($clientFiles.Count -gt 20) 'Unexpectedly small client Python snapshot'
foreach ($file in $clientFiles) {
    $relative = $file.FullName.Substring($clientResolved.Length).TrimStart('\')
    $target = Join-Path $ClientDest $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force -ErrorAction Stop
}
Assert-Condition (Test-Path -LiteralPath (Join-Path $ClientDest 'net.py')) 'Client snapshot copy failed'

$env:MX_CLIENT_SRC = $ClientDest
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$Gate = Join-Path $RepoRoot 'docs\mx15\mx15_quality_gates.py'

function Invoke-Gate([string[]]$Parts) {
    Write-Host "RUN MX15 gate: $($Parts -join ' ')"
    & $Python -B $Gate @Parts
    if ($LASTEXITCODE -ne 0) {
        throw "MX15 gate failed: $($Parts -join ' '), exit $LASTEXITCODE"
    }
}

# Required PR check covers JSON lint, dispatch AST, S2C, targeted incremental,
# 65 P0 full cases and independent singleplayer 2x25.
Invoke-Gate @('selftest')
Invoke-Gate @('lint')

$changed = @()
if ($env:GITHUB_BASE_REF) {
    $base = 'origin/' + $env:GITHUB_BASE_REF
    $changed = @(& $Git -C $RepoRoot diff --name-only --diff-filter=ACMRT "$base...HEAD")
    if ($LASTEXITCODE -ne 0) { $changed = @() }
}
if ($changed.Count -eq 0) {
    # Conservatively run the old 55-case fast suite if the delta cannot be
    # safely identified (e.g., direct push or no fetched base).
    Invoke-Gate @('incremental')
} else {
    Invoke-Gate (@('incremental','--changed') + @($changed))
}

Invoke-Gate @('full')
Write-Host 'MX15 ALL REQUIRED CODE GATES PASS. Native GUI remains NOT_TESTED; release is a separate gate.'
