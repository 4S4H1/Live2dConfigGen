# Run this script explicitly from an elevated PowerShell only when LAN clients
# cannot reach the Host.  It does not open Public-profile or Internet scope.
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [ValidateRange(1024, 65535)][int]$DiscoveryPort = 48765,
    [string]$Program = "$env:LOCALAPPDATA\Programs\L2DUpdateHost\L2DUpdateHost.exe"
)

$ErrorActionPreference = "Stop"
$LegacyRuleName = "L2D Update Host (LocalSubnet)"
$HttpRuleName = "L2D Update Host HTTP (LocalSubnet)"
$DiscoveryRuleName = "L2D Update Host Discovery (LocalSubnet)"
if (-not (Test-Path -LiteralPath $Program -PathType Leaf)) {
    throw "未找到安装后的 Host 程序：$Program"
}
$ResolvedProgram = (Resolve-Path -LiteralPath $Program).Path
$ExistingRules = @(
    @($LegacyRuleName, $HttpRuleName, $DiscoveryRuleName) |
    ForEach-Object {
        Get-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue
    }
)
$NewRules = @()
try {
    $NewRules += @(New-NetFirewallRule `
        -DisplayName $HttpRuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Program $ResolvedProgram `
        -RemoteAddress LocalSubnet `
        -Profile Private,Domain `
        -PassThru)
    $NewRules += @(New-NetFirewallRule `
        -DisplayName $DiscoveryRuleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol UDP `
        -LocalPort $DiscoveryPort `
        -Program $ResolvedProgram `
        -RemoteAddress LocalSubnet `
        -Profile Private,Domain `
        -PassThru)
}
catch {
    if ($NewRules.Count -gt 0) {
        $NewRules | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    }
    throw
}
if ($ExistingRules.Count -gt 0) {
    $ExistingRules | Remove-NetFirewallRule -ErrorAction SilentlyContinue
}
Write-Host "已创建防火墙规则：TCP $Port；UDP $DiscoveryPort"
