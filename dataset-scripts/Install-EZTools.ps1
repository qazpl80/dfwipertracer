<#
.SYNOPSIS
    Installs the .NET 10 runtime (if missing) and downloads MFTECmd, PECmd and
    AmcacheParser (Eric Zimmerman) into -Dest.

.DESCRIPTION
    AmcacheParser is only published as a .NET 10 build, so all three tools use the
    .NET 10 builds and the .NET 10 runtime is installed with Microsoft's
    dotnet-install script when absent. Needs internet access (run before isolating the VM).

.EXAMPLE
    .\Install-EZTools.ps1
    .\Generate-WiperDataset.ps1 -Scenario bcwipe -ToolsDir C:\Tools\EZ
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$Dest = 'C:\Tools\EZ',
    [switch]$SkipDotnet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# ---- .NET 10 runtime
if (-not $SkipDotnet) {
    $dotnet = Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'
    $have = $false
    if (Test-Path $dotnet) {
        $have = [bool](& $dotnet --list-runtimes 2>$null | Select-String 'Microsoft\.NETCore\.App 10\.')
    }
    if ($have) {
        Write-Host '[=] .NET 10 runtime already installed'
    } else {
        Write-Host '[*] Installing .NET 10 runtime'
        $inst = Join-Path $env:TEMP 'dotnet-install.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://dot.net/v1/dotnet-install.ps1' -OutFile $inst
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $inst -Runtime dotnet -Channel 10.0 -InstallDir (Join-Path $env:ProgramFiles 'dotnet')
        if ($LASTEXITCODE -ne 0) { throw "dotnet-install failed (exit $LASTEXITCODE)" }
        Write-Host '[+] .NET 10 runtime installed' -ForegroundColor Green
    }
}

# ---- Zimmerman tools
New-Item -ItemType Directory -Path $Dest -Force | Out-Null
$getter = Join-Path $env:TEMP 'Get-ZimmermanTools.ps1'
Write-Host '[*] Downloading Get-ZimmermanTools.ps1'
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/EricZimmerman/Get-ZimmermanTools/master/Get-ZimmermanTools.ps1' -OutFile $getter

Write-Host "[*] Downloading tools (.NET 10) to $Dest"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $getter -Dest $Dest -NetVersion 10 -NoUpdate
if ($LASTEXITCODE -ne 0) { throw "Get-ZimmermanTools.ps1 failed (exit $LASTEXITCODE)" }

$missing = @()
foreach ($t in 'MFTECmd.exe', 'PECmd.exe', 'AmcacheParser.exe') {
    $f = Get-ChildItem -LiteralPath $Dest -Filter $t -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($f) { Write-Host "[+] $t -> $($f.FullName)" -ForegroundColor Green } else { $missing += $t }
}
if ($missing) { throw "Missing after download: $($missing -join ', '). Re-run (failed downloads are retried)." }

$amc = (Get-ChildItem -LiteralPath $Dest -Filter AmcacheParser.exe -Recurse -File | Select-Object -First 1).FullName
& $amc --help 2>&1 | Select-Object -First 2 | ForEach-Object { Write-Host "    $_" }
Write-Host "`nDone. Use: -ToolsDir $Dest" -ForegroundColor Green
