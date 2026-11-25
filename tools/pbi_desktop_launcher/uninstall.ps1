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

$externalToolsDir = Join-Path $env:ProgramFiles "Common Files\Microsoft Shared\Power BI Desktop\External Tools"
$targetJson = Join-Path $externalToolsDir "semabridge.pbitool.json"

if (Test-Path $targetJson) {
    Remove-Item -Path $targetJson -Force
    Write-Host "Successfully removed SemaBridge registration from: $targetJson"
} else {
    Write-Host "SemaBridge is not currently registered at: $targetJson"
}
