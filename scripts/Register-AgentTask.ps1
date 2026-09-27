#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Register the PropertyBot agent as a daily Windows Task Scheduler job.

.EXAMPLE
  .\scripts\Register-AgentTask.ps1
  .\scripts\Register-AgentTask.ps1 -Time "03:30" -TaskName "PropertyBotAgent"
  .\scripts\Register-AgentTask.ps1 -Unregister
#>
param(
    [string]$Time = "02:00",
    [string]$TaskName = "PropertyBotAgent",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    schtasks /Delete /TN $TaskName /F
    Write-Host "Removed scheduled task '$TaskName'."
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$BatPath = Join-Path $ProjectDir "scripts\run_agent.bat"

if (-not (Test-Path $BatPath)) {
    throw "Batch file not found: $BatPath"
}

schtasks /Create /TN $TaskName /TR "`"$BatPath`"" /SC DAILY /ST $Time /F
if ($LASTEXITCODE -ne 0) {
    throw "schtasks failed with exit code $LASTEXITCODE"
}

Write-Host "Registered daily task '$TaskName' at $Time -> $BatPath"
Write-Host "Verify with: schtasks /Query /TN $TaskName"
