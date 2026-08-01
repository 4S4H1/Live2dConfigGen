param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("WriteManifest", "MergeUnknown", "DeleteManaged")]
    [string] $Mode
)

$ErrorActionPreference = "Stop"
$manifestName = $env:L2D_MANIFEST_NAME
if ([string]::IsNullOrWhiteSpace($manifestName)) {
    throw "Missing installer manifest name"
}

function Resolve-ManagedRoot([string] $Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Missing installer root"
    }
    return [IO.Path]::GetFullPath($Value).TrimEnd([char] 92)
}

if ($Mode -eq "WriteManifest") {
    $root = Resolve-ManagedRoot $env:L2D_MANIFEST_ROOT
    $manifest = [IO.Path]::Combine($root, $manifestName)
    if (Test-Path -LiteralPath $manifest) {
        Remove-Item -LiteralPath $manifest -Force
    }
    $items = @(
        Get-ChildItem -LiteralPath $root -Recurse -File -Force |
            ForEach-Object { $_.FullName.Substring($root.Length).TrimStart([char] 92) }
    )
    $items += $manifestName
    [IO.File]::WriteAllLines(
        $manifest,
        $items,
        [Text.UTF8Encoding]::new($false)
    )
    exit 0
}

if ($Mode -eq "MergeUnknown") {
    $source = Resolve-ManagedRoot $env:L2D_MERGE_SOURCE
    $target = Resolve-ManagedRoot $env:L2D_MERGE_TARGET
    if (-not (Test-Path -LiteralPath $source)) {
        exit 0
    }
    $manifest = [IO.Path]::Combine($source, $manifestName)
    $hasManifest = Test-Path -LiteralPath $manifest
    $managed = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    if ($hasManifest) {
        Get-Content -LiteralPath $manifest |
            ForEach-Object { [void] $managed.Add($_.Trim()) }
    }
    $legacyManaged = $env:L2D_KNOWN_FILES -split [char] 124
    $files = @(Get-ChildItem -LiteralPath $source -Recurse -File -Force)
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($source.Length).TrimStart([char] 92)
        if ($relative -eq $manifestName) {
            continue
        }
        $destination = [IO.Path]::Combine($target, $relative)
        $preserve = if ($hasManifest) {
            -not $managed.Contains($relative)
        } else {
            ($legacyManaged -notcontains $relative) -and
                -not (Test-Path -LiteralPath $destination)
        }
        if (-not $preserve) {
            continue
        }
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) |
            Out-Null
        if (Test-Path -LiteralPath $destination) {
            $base = $destination + $env:L2D_PRESERVE_SUFFIX
            $destination = $base
            $index = 1
            while (Test-Path -LiteralPath $destination) {
                $destination = $base + "." + $index
                $index += 1
            }
        }
        Move-Item -LiteralPath $file.FullName -Destination $destination
    }
    Remove-Item -LiteralPath $source -Recurse -Force
    exit 0
}

$root = Resolve-ManagedRoot $env:L2D_DELETE_ROOT
if (-not (Test-Path -LiteralPath $root)) {
    exit 0
}
$manifest = [IO.Path]::Combine($root, $manifestName)
if (Test-Path -LiteralPath $manifest) {
    $prefix = $root + [char] 92
    $paths = @(Get-Content -LiteralPath $manifest)
    foreach ($relative in $paths) {
        if ([string]::IsNullOrWhiteSpace($relative) -or
            [IO.Path]::IsPathRooted($relative)) {
            continue
        }
        $full = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $relative))
        if ($full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $full -PathType Leaf)) {
            Remove-Item -LiteralPath $full -Force
        }
    }
} else {
    foreach ($name in ($env:L2D_KNOWN_FILES -split [char] 124)) {
        Remove-Item -LiteralPath ([IO.Path]::Combine($root, $name)) -Force -ErrorAction SilentlyContinue
    }
}
Get-ChildItem -LiteralPath $root -Recurse -Directory -Force |
    Sort-Object FullName -Descending |
    ForEach-Object {
        if (-not (Get-ChildItem -LiteralPath $_.FullName -Force)) {
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }
