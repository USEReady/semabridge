# PowerShell uninstaller for SemaBridge Power BI Desktop External Tool registration
# Requires Administrator privileges to remove from Program Files.

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Error "Administrator privileges are required to unregister External Tools. Please run this script in an elevated PowerShell terminal."
    exit 1
}

$dirsToCheck = @(
    (Join-Path $env:ProgramFiles "Common Files\Microsoft Shared\Power BI Desktop\External Tools")
)
if ($env:ProgramFilesX86 -and (Test-Path $env:ProgramFilesX86)) {
    $dir32 = Join-Path $env:ProgramFilesX86 "Common Files\Microsoft Shared\Power BI Desktop\External Tools"
    if ($dir32 -ne $dirsToCheck[0]) {
        $dirsToCheck += $dir32
    }
}

foreach ($dir in $dirsToCheck) {
    $targetJson = Join-Path $dir "semabridge.pbitool.json"
    if (Test-Path $targetJson) {
        Remove-Item -Path $targetJson -Force
        Write-Host "Successfully removed SemaBridge registration from: $targetJson"
    } else {
        Write-Host "SemaBridge is not registered at: $targetJson"
    }
}
