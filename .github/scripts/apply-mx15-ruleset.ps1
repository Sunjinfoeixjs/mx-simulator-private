#Requires -Version 5.1
<#
Apply the MX15 default-branch ruleset to an EXPLICIT private MX repo only.
Requires an administrator-authenticated GitHub CLI; never takes or prints a token.
Dry-run by default. -Apply is required to mutate.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Repo,
    [switch]$Apply
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw 'Repository must be the explicitly verified OWNER/PRIVATE-MX-REPOSITORY'
}
$payload = Join-Path $PSScriptRoot '..\mx15-ruleset-template.json'
$template = Get-Content -LiteralPath $payload -Raw -Encoding UTF8 | ConvertFrom-Json
if ($template.enforcement -ne 'active' -or
    @($template.rules | Where-Object { $_.type -eq 'required_status_checks' }).Count -ne 1) {
    throw 'Ruleset template missing strict status checks'
}
$required=@($template.rules | Where-Object { $_.type -eq 'required_status_checks' } |
    ForEach-Object { $_.parameters.required_status_checks } |
    ForEach-Object { $_.context })
if ($required.Count -ne 2 -or
    $required -notcontains 'mx15/trusted-repository' -or
    $required -notcontains 'mx15/windows-full') {
    throw 'Unexpected required status check contexts'
}
Write-Host "Ruleset target: $Repo, contexts: $($required -join ', ')"
if (-not $Apply) {
    Write-Host 'DRY-RUN ONLY. No GitHub branch or ruleset settings changed.'
    exit 0
}

$gh = (Get-Command gh -ErrorAction Stop).Source
function Fetch-GhJson([string]$endpoint) {
    $data = & $gh api --method GET $endpoint
    if ($LASTEXITCODE -ne 0) { throw "GitHub API access failed: $endpoint" }
    return ($data | ConvertFrom-Json)
}
$info = Fetch-GhJson "repos/$Repo"
if ($info.private -ne $true) {
    throw 'REFUSED: this policy can only target a verified PRIVATE MX repository.'
}
$workflow = Fetch-GhJson "repos/$Repo/contents/.github/workflows/mx15-windows-required.yml"
if ($workflow.path -ne '.github/workflows/mx15-windows-required.yml') {
    throw 'The MX15 workflow is not present on the verified default branch'
}
$runnerData = Fetch-GhJson "repos/$Repo/actions/runners"
$ready = @($runnerData.runners | Where-Object {
    $_.status -eq 'online' -and
    ($_.labels | Where-Object { $_.name -eq 'mx-ci-windows' })
})
if ($ready.Count -lt 1) {
    throw 'No ONLINE Windows runner with mx-ci-windows label; refusing to lock branch.'
}
$existing = @(Fetch-GhJson "repos/$Repo/rulesets")
if (@($existing | Where-Object { $_.name -eq $template.name }).Count -gt 0) {
    throw 'Ruleset name already exists. Inspect and update rather than duplicating.'
}
& $gh api --method POST "repos/$Repo/rulesets" --input $payload
if ($LASTEXITCODE -ne 0) {
    throw 'Ruleset creation failed; check Administration(write) permission and plan.'
}
Write-Host 'Ruleset API returned success. Verify protection rules in GitHub UI.'
