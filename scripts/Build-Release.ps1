[CmdletBinding()]
param(
    [string]$KeyDirectory = "$env:LOCALAPPDATA\4S4H1\release-keys",
    [string]$Notes = "1.4.4：修复数据精度、撤销恢复、文件重命名、AI 取消与异常处理、更新下载及安装文件管理问题。",
    [string]$MinimumSupportedVersion = "1.0.0"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PrivateKey = Join-Path $KeyDirectory "release_private_key.pem"
$PublicKey = Join-Path $KeyDirectory "release_public_key.pem"
$RepositoryPublicKey = Join-Path $RepoRoot "l2d_config_editor\assets\release_public_key.pem"
$PythonExe = Join-Path $RepoRoot ".tools\python-3.13.14\python.exe"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$MakeNsis = Join-Path $RepoRoot ".tools\nsis-3.12\makensis.exe"
$ReleaseDir = Join-Path $RepoRoot "dist\release"
$ReleaseStageDir = Join-Path $RepoRoot "dist\release-staging"
$ReleaseBackupDir = Join-Path $RepoRoot "dist\release-backup"
$PyInstallerDist = Join-Path $RepoRoot "dist\pyinstaller"
$WorkDir = Join-Path $RepoRoot "build\pyinstaller"
$OriginalPath = $env:PATH
$UvExecutable = (Get-Command uv -CommandType Application -ErrorAction Stop).Source

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step 失败，退出码 $LASTEXITCODE"
    }
}

function Clear-KnownBuildDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [switch]$Create
    )
    $normalizedTarget = [System.IO.Path]::GetFullPath($Target).TrimEnd('\')
    $allowedTargets = @(
        $ReleaseDir,
        $ReleaseStageDir,
        $ReleaseBackupDir,
        $PyInstallerDist,
        $WorkDir,
        (Join-Path $RepoRoot "build\icons"),
        (Join-Path $RepoRoot "build\licenses")
    ) | ForEach-Object { [System.IO.Path]::GetFullPath($_).TrimEnd('\') }
    if ($allowedTargets -notcontains $normalizedTarget) {
        throw "拒绝清理未列入白名单的目录：$normalizedTarget"
    }
    if ($normalizedTarget -eq [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')) {
        throw "拒绝清理仓库根目录"
    }
    if (Test-Path -LiteralPath $normalizedTarget) {
        Remove-Item -LiteralPath $normalizedTarget -Recurse -Force
    }
    if ($Create) {
        New-Item -ItemType Directory -Force -Path $normalizedTarget | Out-Null
    }
}

if (-not (Test-Path -LiteralPath $PrivateKey -PathType Leaf)) {
    throw "缺少发布私钥。先运行 scripts\Initialize-ReleaseKey.ps1"
}
if (-not (Test-Path -LiteralPath $PublicKey -PathType Leaf)) {
    throw "缺少发布公钥。先运行 scripts\Initialize-ReleaseKey.ps1"
}
if (-not (Test-Path -LiteralPath $RepositoryPublicKey -PathType Leaf)) {
    throw "缺少仓库内嵌发布公钥：$RepositoryPublicKey"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "未找到固定 Python 3.13.14：$PythonExe"
}
if ((& $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim() -ne "3.13.14") {
    throw "固定 Python 运行时版本不是 3.13.14"
}
if (-not (Test-Path -LiteralPath $MakeNsis -PathType Leaf)) {
    throw "未找到仓库隔离的 NSIS 3.12：$MakeNsis"
}
$NsisVersion = (& $MakeNsis /VERSION).TrimStart("v")
if ([version]$NsisVersion -ne [version]"3.12") {
    throw "NSIS 版本不匹配：检测到 $NsisVersion，发布要求 3.12。"
}

Push-Location $RepoRoot
try {
    # PyInstaller searches PATH for transitive DLL dependencies. Other desktop
    # tools can expose incompatible ICU/OpenSSL/UCRT builds under the same names.
    # Only the locked toolchain and Windows system runtime may participate.
    $env:PATH = @(
        (Join-Path $RepoRoot ".venv\Scripts"),
        (Split-Path -Parent $PythonExe),
        (Join-Path $env:WINDIR "System32"),
        $env:WINDIR,
        (Join-Path $env:WINDIR "System32\Wbem"),
        (Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0"),
        $PSHOME
    ) -join [System.IO.Path]::PathSeparator
    $env:UV_CACHE_DIR = Join-Path $RepoRoot ".uv-cache"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $RepoRoot ".uv-python"
    & $UvExecutable sync --python $PythonExe --extra build --locked
    Assert-NativeSuccess "同步锁定环境"
    & $VenvPython scripts/release_tools.py verify-environment
    Assert-NativeSuccess "校验固定工具链版本"
    $Version = (& $VenvPython -c "from l2d_config_editor.version import VERSION; print(VERSION)").Trim()
    Assert-NativeSuccess "读取产品版本"
    & $VenvPython scripts/release_tools.py verify-keypair --private $PrivateKey --public $PublicKey --embedded-public $RepositoryPublicKey
    Assert-NativeSuccess "校验发布密钥"
    Clear-KnownBuildDirectory (Join-Path $RepoRoot "build\icons") -Create
    Clear-KnownBuildDirectory (Join-Path $RepoRoot "build\licenses") -Create
    & $VenvPython scripts/generate-icons.py
    Assert-NativeSuccess "生成图标"
    & $VenvPython scripts/release_tools.py version-info --output build/windows/version_info.txt
    Assert-NativeSuccess "生成编辑器版本资源"
    & $VenvPython scripts/release_tools.py version-info --host --output build/windows/host_version_info.txt
    Assert-NativeSuccess "生成 Host 版本资源"
    & $VenvPython scripts/release_tools.py collect-licenses --output build/licenses
    Assert-NativeSuccess "收集第三方许可证"
    & $VenvPython scripts/run_tests.py
    Assert-NativeSuccess "运行全量测试"

    Clear-KnownBuildDirectory $ReleaseStageDir -Create
    Clear-KnownBuildDirectory $ReleaseBackupDir
    Clear-KnownBuildDirectory $PyInstallerDist -Create
    Clear-KnownBuildDirectory $WorkDir -Create
    & $VenvPython -m PyInstaller --noconfirm --clean --distpath $PyInstallerDist --workpath $WorkDir packaging/L2DConfigEditor.spec
    Assert-NativeSuccess "构建编辑器 onedir"
    & $VenvPython -m PyInstaller --noconfirm --clean --distpath $PyInstallerDist --workpath $WorkDir packaging/L2DUpdateHost.spec
    Assert-NativeSuccess "构建独立 Host onedir"
    & $VenvPython scripts/smoke_frozen_editor.py
    Assert-NativeSuccess "验收成品编辑器启动、JSON 打开及单实例转交"

    $EditorSourceDir = Join-Path $PyInstallerDist "L2DConfigEditor"
    $HostSourceDir = Join-Path $PyInstallerDist "L2DUpdateHost"
    if (-not (Test-Path -LiteralPath (Join-Path $HostSourceDir "L2DUpdateHost.exe") -PathType Leaf)) {
        throw "Host onedir 未生成预期入口：$HostSourceDir"
    }
    & $MakeNsis "/DVERSION=$Version" "/DSOURCE_DIR=$EditorSourceDir" "/DHOST_SOURCE_DIR=$HostSourceDir" "/DOUTPUT_DIR=$ReleaseStageDir" packaging/editor-installer.nsi
    Assert-NativeSuccess "构建双程序安装器"

    $EditorInstaller = Join-Path $ReleaseStageDir "L2DConfigEditor-Setup-$Version-x64.exe"
    if (-not (Test-Path -LiteralPath $EditorInstaller -PathType Leaf)) {
        throw "编辑器安装器未生成：$EditorInstaller"
    }
    $Bundle = Join-Path $ReleaseStageDir "L2DConfigEditor-$Version.l2dupdate"
    & $VenvPython scripts/release_tools.py bundle --installer $EditorInstaller --private-key $PrivateKey --output $Bundle --notes $Notes --minimum-supported-version $MinimumSupportedVersion
    Assert-NativeSuccess "签名更新包"
    Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination $ReleaseStageDir -Force
    Copy-Item -LiteralPath "build\licenses" -Destination $ReleaseStageDir -Recurse -Force
    & $VenvPython scripts/release_tools.py checksums --directory $ReleaseStageDir --output (Join-Path $ReleaseStageDir "SHA256SUMS.txt")
    Assert-NativeSuccess "生成 SHA-256 清单"
    $ExpectedReleaseFiles = @(
        [System.IO.Path]::GetFileName($EditorInstaller),
        [System.IO.Path]::GetFileName($Bundle),
        "THIRD_PARTY_NOTICES.md",
        "SHA256SUMS.txt"
    )
    foreach ($ExpectedReleaseFile in $ExpectedReleaseFiles) {
        $ExpectedPath = Join-Path $ReleaseStageDir $ExpectedReleaseFile
        if (-not (Test-Path -LiteralPath $ExpectedPath -PathType Leaf)) {
            throw "发布目录缺少预期文件：$ExpectedPath"
        }
    }
    $UnexpectedReleaseFiles = Get-ChildItem -LiteralPath $ReleaseStageDir -File |
        Where-Object { $_.Name -notin $ExpectedReleaseFiles }
    if ($UnexpectedReleaseFiles) {
        throw "发布目录包含意外文件：$($UnexpectedReleaseFiles.Name -join ', ')"
    }
    $ReleaseDirectories = @(Get-ChildItem -LiteralPath $ReleaseStageDir -Directory)
    if (
        $ReleaseDirectories.Count -ne 1 -or
        $ReleaseDirectories[0].Name -ne "licenses"
    ) {
        throw "发布目录的子目录必须且只能是 licenses"
    }
    if (
        (Test-Path -LiteralPath $ReleaseDir) -and
        -not (Test-Path -LiteralPath $ReleaseDir -PathType Container)
    ) {
        throw "正式发布路径不是目录：$ReleaseDir"
    }
    $HadPreviousRelease = Test-Path -LiteralPath $ReleaseDir -PathType Container
    if ($HadPreviousRelease) {
        Move-Item -LiteralPath $ReleaseDir -Destination $ReleaseBackupDir
    }
    try {
        Move-Item -LiteralPath $ReleaseStageDir -Destination $ReleaseDir
    }
    catch {
        if (
            $HadPreviousRelease -and
            (Test-Path -LiteralPath $ReleaseBackupDir -PathType Container) -and
            -not (Test-Path -LiteralPath $ReleaseDir)
        ) {
            Move-Item -LiteralPath $ReleaseBackupDir -Destination $ReleaseDir
        }
        throw
    }
    Clear-KnownBuildDirectory $ReleaseBackupDir
    Write-Host "发布完成：$ReleaseDir"
}
finally {
    $env:PATH = $OriginalPath
    Pop-Location
}
