[CmdletBinding()]
param(
    [string]$KeyDirectory = "$env:LOCALAPPDATA\4S4H1\release-keys"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PrivateKey = Join-Path $KeyDirectory "release_private_key.pem"
$PublicKey = Join-Path $KeyDirectory "release_public_key.pem"
$RepositoryPublicKey = Join-Path $RepoRoot "l2d_config_editor\assets\release_public_key.pem"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Push-Location $RepoRoot
try {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        throw "缺少锁定环境。请先运行 uv sync --extra build --locked。"
    }
    & $VenvPython scripts/release_tools.py init-key --private $PrivateKey --public $PublicKey
    if ($LASTEXITCODE -ne 0) {
        throw "创建发布密钥失败，退出码 $LASTEXITCODE"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $RepositoryPublicKey) | Out-Null
    Copy-Item -LiteralPath $PublicKey -Destination $RepositoryPublicKey
    if ($IsWindows -or $env:OS -eq "Windows_NT") {
        $Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls.exe $PrivateKey /inheritance:r /grant:r "${Identity}:(R,W)" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "收紧私钥 ACL 失败，退出码 $LASTEXITCODE"
        }
    }
    Write-Host "私钥（请离线备份，禁止提交）：$PrivateKey"
    Write-Host "公钥（构建 Host 时使用）：$PublicKey"
    Write-Host "仓库内嵌公钥（应提交）：$RepositoryPublicKey"
}
finally {
    Pop-Location
}
