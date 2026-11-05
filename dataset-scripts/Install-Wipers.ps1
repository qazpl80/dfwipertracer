<#
.SYNOPSIS
    Installs CCleaner (downloaded) and BCWipe (from an installer you supply) in a lab VM.

.DESCRIPTION
    CCleaner: downloads the free installer from download.ccleaner.com and installs silently (/S).
    BCWipe:   Jetico gates the trial download behind its website, so download the Windows
              trial from https://jetico.com/downloads/data-wiping/ first and pass it with
              -BCWipeInstaller. Silent switches differ between installer builds; the default
              is '/S' for .exe and 'msiexec /qn' for .msi. Override with -BCWipeInstallArgs.

    Notes
      * The BCWipe trial is 21 days and one-pass only. Command-line/scripting support is
        listed under Jetico's Enterprise edition, so confirm `bcwipe.exe` runs in your trial.
      * Take a VM snapshot after this so each scenario can start from the same state.

.EXAMPLE
    .\Install-Wipers.ps1 -BCWipeInstaller C:\Users\analyst\Downloads\bcwipe_setup.exe
    .\Install-Wipers.ps1 -SkipBCWipe
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$BCWipeInstaller,
    [string]$BCWipeInstallArgs = '/S',
    [string]$CCleanerInstaller,
    [string]$CCleanerUrl = 'https://download.ccleaner.com/ccsetup.exe',
    [switch]$SkipCCleaner,
    [switch]$SkipBCWipe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Find-Installed([string[]]$Candidates) {
    foreach ($c in $Candidates) { if (Test-Path -LiteralPath $c) { return $c } }
    $null
}
$pf = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }

$ccPaths = $pf | ForEach-Object { Join-Path $_ 'CCleaner\CCleaner64.exe'; Join-Path $_ 'CCleaner\CCleaner.exe' }
$bcPaths = $pf | ForEach-Object { Join-Path $_ 'Jetico\BCWipe\bcwipe.exe'; Join-Path $_ 'Jetico\BCWipe\BCWipe.exe' }

# ---- CCleaner
if (-not $SkipCCleaner) {
    $existing = Find-Installed $ccPaths
    if ($existing) {
        Write-Host "[=] CCleaner already installed: $existing"
    } else {
        $inst = $CCleanerInstaller
        if (-not $inst) {
            $inst = Join-Path $env:TEMP 'ccsetup.exe'
            Write-Host "[*] Downloading CCleaner from $CCleanerUrl"
            Invoke-WebRequest -UseBasicParsing -Uri $CCleanerUrl -OutFile $inst
        }
        if (-not (Test-Path -LiteralPath $inst)) { throw "CCleaner installer not found: $inst" }
        Write-Host '[*] Installing CCleaner silently'
        $p = Start-Process -FilePath $inst -ArgumentList '/S' -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "CCleaner installer exit code $($p.ExitCode)" }
        $found = Find-Installed $ccPaths
        if (-not $found) { throw 'CCleaner install finished but CCleaner64.exe/CCleaner.exe was not found.' }
        Write-Host "[+] CCleaner -> $found" -ForegroundColor Green
    }
}

# ---- BCWipe
if (-not $SkipBCWipe) {
    $existing = Find-Installed $bcPaths
    if ($existing) {
        Write-Host "[=] BCWipe already installed: $existing"
    } else {
        if (-not $BCWipeInstaller) {
            Write-Host '[!] BCWipe: no installer given.' -ForegroundColor Yellow
            Write-Host '    1. Download the Windows free trial: https://jetico.com/downloads/data-wiping/'
            Write-Host '    2. Re-run: .\Install-Wipers.ps1 -SkipCCleaner -BCWipeInstaller <path-to-installer>'
        } else {
            if (-not (Test-Path -LiteralPath $BCWipeInstaller)) { throw "BCWipe installer not found: $BCWipeInstaller" }
            Write-Host '[*] Installing BCWipe silently'
            if ($BCWipeInstaller -like '*.msi') {
                $p = Start-Process msiexec.exe -ArgumentList "/i `"$BCWipeInstaller`" /qn /norestart" -Wait -PassThru
            } else {
                $p = Start-Process -FilePath $BCWipeInstaller -ArgumentList $BCWipeInstallArgs -Wait -PassThru
            }
            # 3010 = success, reboot required
            if ($p.ExitCode -notin 0, 3010) { throw "BCWipe installer exit code $($p.ExitCode). Try a different -BCWipeInstallArgs or install via the GUI." }
            $found = Find-Installed $bcPaths
            if ($found) {
                Write-Host "[+] BCWipe -> $found" -ForegroundColor Green
                if ($p.ExitCode -eq 3010) { Write-Host '[!] Reboot required before use.' -ForegroundColor Yellow }
            } else {
                Write-Host '[!] Installer ran but bcwipe.exe was not found in Program Files\Jetico\BCWipe. Locate it and pass -BCWipePath to Generate-WiperDataset.ps1.' -ForegroundColor Yellow
            }
        }
    }
}

Write-Host "`nNext: close any open app windows, take a VM snapshot ('clean-base'), then run Generate-WiperDataset.ps1." -ForegroundColor Cyan
