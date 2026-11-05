<#
.SYNOPSIS
    Generic silent installer for any wiper tool (download or local installer).

.DESCRIPTION
    Installs an .exe/.msi (or extracts a .zip) and verifies that the expected executable exists.
    Use it for tools Install-Wipers.ps1 does not cover.

.EXAMPLE
    # Download + silent install (NSIS-style /S), then verify the exe
    .\Install-Tool.ps1 -Name mytool -Url https://example.com/setup.exe -SilentArgs '/S' -ExpectExe 'C:\Program Files\MyTool\mytool.exe'

    # Local MSI
    .\Install-Tool.ps1 -Name mytool -InstallerPath C:\dl\mytool.msi -ExpectExe 'C:\Program Files\MyTool\mytool.exe'

    # Portable zip (e.g. SDelete): extracted to -Dest, no installer run
    .\Install-Tool.ps1 -Name sdelete -Url https://download.sysinternals.com/files/SDelete.zip -Dest C:\Tools\sdelete -ExpectExe C:\Tools\sdelete\sdelete64.exe

    # Then:
    .\Generate-WiperDataset.ps1 -Scenario sdelete -ToolsDir C:\Tools\EZ -WiperPath C:\Tools\sdelete\sdelete64.exe
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Name,
    [string]$Url,
    [string]$InstallerPath,
    # Common switches: NSIS '/S', Inno '/VERYSILENT /NORESTART', MSI is handled automatically.
    [string]$SilentArgs = '/S',
    # Extraction folder for .zip installers
    [string]$Dest,
    # Path that must exist afterwards; the script fails if it doesn't.
    [string]$ExpectExe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

if ($ExpectExe -and (Test-Path -LiteralPath $ExpectExe)) { Write-Host "[=] $Name already present: $ExpectExe"; return }

$file = $InstallerPath
if (-not $file) {
    if (-not $Url) { throw 'Give -Url or -InstallerPath.' }
    $file = Join-Path $env:TEMP ([IO.Path]::GetFileName(([uri]$Url).AbsolutePath))
    Write-Host "[*] Downloading $Url"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $file
}
if (-not (Test-Path -LiteralPath $file)) { throw "Installer not found: $file" }

switch -Wildcard ($file) {
    '*.zip' {
        if (-not $Dest) { throw '-Dest is required for .zip' }
        New-Item -ItemType Directory -Path $Dest -Force | Out-Null
        Expand-Archive -LiteralPath $file -DestinationPath $Dest -Force
    }
    '*.msi' {
        $p = Start-Process msiexec.exe -ArgumentList "/i `"$file`" /qn /norestart" -Wait -PassThru
        if ($p.ExitCode -notin 0, 3010) { throw "msiexec exit code $($p.ExitCode)" }
    }
    default {
        $p = Start-Process -FilePath $file -ArgumentList $SilentArgs -Wait -PassThru
        if ($p.ExitCode -notin 0, 3010) { throw "Installer exit code $($p.ExitCode); try different -SilentArgs or install via GUI." }
    }
}

if ($ExpectExe -and -not (Test-Path -LiteralPath $ExpectExe)) { throw "$Name installed but $ExpectExe not found. Locate the exe and pass it as -WiperPath." }
Write-Host "[+] $Name ready" -ForegroundColor Green
