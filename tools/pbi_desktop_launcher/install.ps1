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

# Detect candidate External Tools directories:
# 1. 64-bit Power BI Desktop: %ProgramFiles%\Common Files\Microsoft Shared\Power BI Desktop\External Tools
# 2. 32-bit Power BI Desktop: %ProgramFiles(x86)%\Common Files\Microsoft Shared\Power BI Desktop\External Tools
$targetDirs = @()

$dir64 = Join-Path $env:ProgramFiles "Common Files\Microsoft Shared\Power BI Desktop\External Tools"
$targetDirs += $dir64

if ($env:ProgramFilesX86 -and (Test-Path $env:ProgramFilesX86)) {
    $dir32 = Join-Path $env:ProgramFilesX86 "Common Files\Microsoft Shared\Power BI Desktop\External Tools"
    $pbi32Installed = Test-Path (Join-Path $env:ProgramFilesX86 "Microsoft Power BI Desktop\bin\PBIDesktop.exe")
    if ($pbi32Installed -or (Test-Path $dir32)) {
        if ($dir32 -ne $dir64) {
            $targetDirs += $dir32
        }
    }
}

# Read template and set absolute path
$jsonContent = Get-Content $sourceJson -Raw -Encoding UTF8 | ConvertFrom-Json
$jsonContent.path = $launcherCmd

Write-Host "Registering SemaBridge in Power BI Desktop External Tools..."
foreach ($dir in $targetDirs) {
    if (-not (Test-Path $dir)) {
        Write-Host "Detected active Power BI Desktop target path (creating): $dir"
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    } else {
        Write-Host "Detected active Power BI Desktop target path: $dir"
    }

    $targetJson = Join-Path $dir "semabridge.pbitool.json"
    $jsonContent | ConvertTo-Json -Depth 5 | Set-Content $targetJson -Encoding UTF8
    Write-Host "Registered at: $targetJson"
}

Write-Host "Successfully registered SemaBridge in Power BI Desktop External Tools!"
Write-Host "Launcher path: $launcherCmd"
