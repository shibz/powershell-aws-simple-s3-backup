<#
.SYNOPSIS
	Generates a manifest for the module
	and bundles all of the module source files
	and manifest into a distributable ZIP file.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [version]$ModuleVersion
)

$ErrorActionPreference = "Stop"

Write-Host "Building release for v$moduleVersion"

$scriptPath = Split-Path -LiteralPath $(if ($PSVersionTable.PSVersion.Major -ge 3) { $PSCommandPath } else { & { $MyInvocation.ScriptName } })

$src = (Join-Path (Split-Path $scriptPath) 'src')
$dist = (Join-Path (Split-Path $scriptPath) 'dist')
if (Test-Path $dist) {
    Remove-Item $dist -Force -Recurse
}
New-Item $dist -ItemType Directory | Out-Null

Write-Host "Creating module manifest..."

$manifestFileName = Join-Path $dist 'AwsSimpleS3Backup.psd1'

New-ModuleManifest `
    -Path $manifestFileName `
    -ModuleVersion $ModuleVersion `
    -Guid 5229c29a-0a78-4286-8aa0-d5e11fdca4c4 `
    -Author 'Nathan Przybyszewski' `
    -Copyright '(c) $((Get-Date).Year) Nathan Przybyszewski. All rights reserved.' `
    -Description 'Simple S3 Backup PowerShell Module.' `
    -PowerShellVersion '3.0' `
    -DotNetFrameworkVersion '4.5' `
    -NestedModules (Get-ChildItem $src -Exclude *.psd1 | % { $_.Name })

Write-Host "Creating release archive..."

# Copy the distributable files to the dist folder.
Copy-Item -Path "$src\*" `
          -Destination $dist `
          -Recurse

# Requires .NET 4.5
[Reflection.Assembly]::LoadWithPartialName("System.IO.Compression.FileSystem") | Out-Null

$zipFileName = Join-Path ([System.IO.Path]::GetDirectoryName($dist)) "$([System.IO.Path]::GetFileNameWithoutExtension($manifestFileName))-$ModuleVersion.zip"

# Overwrite the ZIP if it already already exists.
if (Test-Path $zipFileName) {
    Remove-Item $zipFileName -Force
}

$compressionLevel = [System.IO.Compression.CompressionLevel]::Optimal
$includeBaseDirectory = $false
[System.IO.Compression.ZipFile]::CreateFromDirectory($dist, $zipFileName, $compressionLevel, $includeBaseDirectory)

Move-Item $zipFileName $dist -Force
