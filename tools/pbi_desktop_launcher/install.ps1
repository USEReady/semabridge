# PowerShell installer for SemaBridge Power BI Desktop External Tool registration
# Requires Administrator privileges to write to Program Files.

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Error "Administrator privileges are required to register External Tools in Power BI Desktop. Please run this script in an elevated PowerShell terminal."
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcherCmd = Join-Path $scriptDir "launcher.cmd"
$sourceJson = Join-Path $scriptDir "semabridge.pbitool.json"

if (-not (Test-Path $launcherCmd)) {
    Write-Error "Launcher script not found at: $launcherCmd"
    exit 1
}

if (-not (Test-Path $sourceJson)) {
    Write-Error "pbitool definition not found at: $sourceJson"
    exit 1
}

# External Tools directory confirmed for both standard MSI and Microsoft Store installs:
$externalToolsDir = Join-Path $env:ProgramFiles "Common Files\Microsoft Shared\Power BI Desktop\External Tools"

if (-not (Test-Path $externalToolsDir)) {
    Write-Host "Creating External Tools directory: $externalToolsDir"
    New-Item -ItemType Directory -Force -Path $externalToolsDir | Out-Null
}

# Read template and set absolute path
$jsonContent = Get-Content $sourceJson -Raw -Encoding UTF8 | ConvertFrom-Json
$jsonContent.path = $launcherCmd

$targetJson = Join-Path $externalToolsDir "semabridge.pbitool.json"
$jsonContent | ConvertTo-Json -Depth 5 | Set-Content $targetJson -Encoding UTF8

Write-Host "Successfully registered SemaBridge in Power BI Desktop External Tools!"
Write-Host "Registration folder: $externalToolsDir"
Write-Host "Configuration file:  $targetJson"
Write-Host "Target executable:   $launcherCmd"
