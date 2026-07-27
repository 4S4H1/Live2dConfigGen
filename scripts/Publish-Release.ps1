[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Bundle,
    [string]$DataRoot = "$env:LOCALAPPDATA\4S4H1\L2DUpdateHost",
    [string]$PublicKey = "$env:LOCALAPPDATA\4S4H1\release-keys\release_public_key.pem"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BundlePath = (Resolve-Path -LiteralPath $Bundle).Path
$PublicKeyPath = (Resolve-Path -LiteralPath $PublicKey).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Push-Location $RepoRoot
try {
    & $VenvPython -c "import sys; from pathlib import Path; from l2d_config_editor.update_manifest import import_release_bundle, load_public_key; key=load_public_key(Path(sys.argv[3]).read_bytes()); print('已发布版本', import_release_bundle(Path(sys.argv[1]), Path(sys.argv[2])/'releases', key, retain=2))" $BundlePath $DataRoot $PublicKeyPath
    if ($LASTEXITCODE -ne 0) {
        throw "发布更新包失败，退出码 $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
