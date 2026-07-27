# Run this script explicitly from an elevated PowerShell only when LAN clients
# cannot reach the Host.  It does not open Public-profile or Internet scope.
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [string]$Program = "$env:LOCALAPPDATA\Programs\L2DUpdateHost\L2DUpdateHost.exe"
)

$ErrorActionPreference = "Stop"
$RuleName = "L2D Update Host (LocalSubnet)"
if (-not (Test-Path -LiteralPath $Program -PathType Leaf)) {
    throw "未找到 Host 程序：$Program"
}
$ResolvedProgram = (Resolve-Path -LiteralPath $Program).Path
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -Program $ResolvedProgram `
    -RemoteAddress LocalSubnet `
    -Profile Private,Domain | Out-Null
Write-Host "已创建防火墙规则：$RuleName"
