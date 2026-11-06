<#
.SYNOPSIS
    Generates a labeled ground-truth forensic dataset for WiperTrace.

.DESCRIPTION
    Phase 1  Plant a controlled corpus in D:\corpus (-TargetDir) and record ground truth.
    Phase 2  Run the selected wiper headlessly (start/end UTC recorded).
    Phase 3  Parse $MFT, $J, Prefetch and Amcache with Eric Zimmerman tools.
    Phase 4  Write ground_truth.json (manifest) next to the CSVs.

    Output: <OutputDir>\<Scenario>\  (re-running a scenario replaces that folder).
    CSV names match WiperTrace discovery patterns:
      mftecmd_$J_Output.csv, mftecmd_$MFT_Output.csv,
      pecmd_PECmd_Output.csv, amcache_*FileEntries.csv

    Must run elevated, inside a disposable VM.

.EXAMPLE
    .\Generate-WiperDataset.ps1 -Scenario bcwipe   -ToolsDir C:\Tools\EZ
    .\Generate-WiperDataset.ps1 -Scenario ccleaner -ToolsDir C:\Tools\EZ -FileCount 20
    # Any other tool (custom scenario name):
    .\Generate-WiperDataset.ps1 -Scenario mytool -ToolsDir C:\Tools\EZ `
        -WiperPath 'C:\Tools\mytool.exe' -WiperArgs '--shred "{TARGET}"' -StrictWipeCheck
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    # Label + output folder name. Built-in profiles: bcwipe, ccleaner, sdelete, eraser, bleachbit,
    # normal_delete. Any other name is a custom tool: supply -WiperPath and -WiperArgs.
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9_.-]+$')]
    [string]$Scenario,

    [ValidateRange(1, 10000)]
    [int]$FileCount = 10,

    [string]$OutputDir = 'C:\ForensicDataset',

    [Parameter(Mandatory)]
    [string]$ToolsDir,

    # Wiper executable. Optional for built-in profiles (auto-detected in Program Files / PATH).
    [string]$WiperPath,

    # Override/define the command line(s). One string per step, run in order.
    # Tokens: {TARGET} = target folder, {DRIVE} = target drive (e.g. D:).
    # Example: -WiperArgs 'Delete -UD1 "{TARGET}\*.*"'
    [string[]]$WiperArgs,

    # Exit codes treated as success for -WiperArgs steps.
    [int[]]$WiperOkExitCodes = @(0, 1),

    # Process names to wait on after the command returns (for tools that hand off to a worker).
    [string[]]$WaitProcess = @(),

    # Max minutes to wait for the corpus files to disappear after the wiper returns.
    [int]$WipeTimeoutMinutes = 5,

    # Corpus folder (recreated every run). Must be on an NTFS volume.
    [string]$TargetDir = 'D:\corpus',

    [int]$SettleSeconds = 5,

    # Fail (instead of warn) if target files still exist after the wiper ran.
    [switch]$StrictWipeCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TargetDir   = $TargetDir.TrimEnd('\')
$TargetDrive = [IO.Path]::GetPathRoot($TargetDir).TrimEnd('\')   # e.g. D:
$CaseDir     = Join-Path $OutputDir $Scenario
$script:Step = 0

function Write-Phase([string]$Msg) {
    Write-Host ''
    Write-Host ("=== {0} ===" -f $Msg) -ForegroundColor Cyan
}
function Write-Info([string]$Msg) { Write-Host "  [*] $Msg" }
function Write-Ok([string]$Msg)   { Write-Host "  [+] $Msg" -ForegroundColor Green }
function Write-Warn2([string]$Msg){ Write-Host "  [!] $Msg" -ForegroundColor Yellow }

function Find-Tool([string]$Explicit, [string[]]$Names, [string[]]$SearchDirs) {
    if ($Explicit) {
        if (Test-Path -LiteralPath $Explicit -PathType Leaf) { return (Resolve-Path -LiteralPath $Explicit).Path }
        throw "Specified tool not found: $Explicit"
    }
    foreach ($d in $SearchDirs) {
        foreach ($n in $Names) {
            $p = Join-Path $d $n
            if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
        }
    }
    foreach ($n in $Names) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return $null
}

function Invoke-Exe {
    param([string]$Exe, [string]$Arguments, [string]$LogName, [int[]]$OkCodes = @(0))
    $log = Join-Path $CaseDir $LogName
    Write-Info "$([IO.Path]::GetFileName($Exe)) $Arguments"
    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    # GUI-subsystem exes (e.g. BCWipe.exe) can report no exit code; treat that as 0.
    $code = if ($null -eq $p.ExitCode) { 0 } else { [int]$p.ExitCode }
    if ($OkCodes -notcontains $code) {
        throw "$([IO.Path]::GetFileName($Exe)) exited with code $code. See $log"
    }
    return $code
}

# ---------------------------------------------------------------- Preflight
Write-Phase 'Preflight'

if (-not (Test-Path -LiteralPath $ToolsDir -PathType Container)) { throw "ToolsDir not found: $ToolsDir" }
$ez = @{}
foreach ($t in 'MFTECmd.exe', 'PECmd.exe', 'AmcacheParser.exe') {
    $p = Get-ChildItem -LiteralPath $ToolsDir -Filter $t -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $p) { throw "$t not found under $ToolsDir" }
    $ez[$t] = $p.FullName
    Write-Ok "$t -> $($p.FullName)"
}

# ---- Wiper profiles. Add a tool here (or just use -WiperPath/-WiperArgs for a one-off).
#   Exe   : executable names to look for      Dirs : subfolders of Program Files to search
#   Steps : @{ Args; Ok } run in order        Wait : process names to wait on afterwards
#   Pre   : optional scriptblock run before the first step (receives the exe path)
$Profiles = @{
    'bcwipe' = @{
        Exe = @('BCWipe.exe'); Dirs = @('Jetico\BCWipe')
        # Documented: BCWipe Delete [-Mode] <files>; UD1 = 1 pass (the 21-day trial allows only 1 pass).
        # \*.* wipes the files but keeps the folder. BCWipe.exe is GUI-subsystem and reports no exit code.
        Steps = @(@{ Args = 'Delete -UD1 "{TARGET}\*.*"'; Ok = @(0, 1) })
    }
    'ccleaner' = @{
        Exe = @('CCleaner64.exe', 'CCleaner.exe'); Dirs = @('CCleaner')
        Steps = @(@{ Args = '/AUTO'; Ok = @(0, 1) })
        Wait = @('CCleaner64', 'CCleaner')
        # /AUTO only runs the saved profile, so register the corpus as a custom Include folder.
        Pre = {
            param($Exe)
            $reg = 'HKCU:\Software\Piriform\CCleaner'
            if (-not (Test-Path $reg)) { New-Item -Path $reg -Force | Out-Null }
            Set-ItemProperty -Path $reg -Name '(App)Include1' -Value "PATH|$TargetDir|*.*|RECURSE"
            Set-ItemProperty -Path $reg -Name '(App)Custom Files' -Value 'True'
            Set-ItemProperty -Path $reg -Name 'SecureDeleteType' -Value 0 -Type DWord   # 0 = simple overwrite (1 pass)
            Set-ItemProperty -Path $reg -Name 'WipeFreeSpaceDrives' -Value $TargetDrive -ErrorAction SilentlyContinue
            $ini = Join-Path (Split-Path $Exe) 'ccleaner.ini'
            if (Test-Path $ini) {
                $wipeNotes.Add("Portable ccleaner.ini found at $ini; custom include must be configured there manually.")
                Write-Warn2 $wipeNotes[-1]
            }
        }
    }
    'sdelete' = @{
        Exe = @('sdelete64.exe', 'sdelete.exe'); Dirs = @()
        Steps = @(@{ Args = '-nobanner -accepteula -p 1 -r -q "{TARGET}\*"'; Ok = @(0) })
    }
    'eraser' = @{
        Exe = @('Eraser.exe'); Dirs = @('Eraser')
        Steps = @(
            @{ Args = 'add --action=erase --target=file --path="{TARGET}\*"'; Ok = @(0) }
            @{ Args = 'run'; Ok = @(0) }
        )
    }
    'bleachbit' = @{
        Exe = @('bleachbit_console.exe'); Dirs = @('BleachBit')
        Steps = @(@{ Args = '--shred "{TARGET}"'; Ok = @(0) })
    }
    'normal_delete' = @{ Builtin = $true }   # negative control: plain Remove-Item
}

$prof = $null
if ($Profiles.ContainsKey($Scenario)) {
    $prof = $Profiles[$Scenario]
} elseif ($WiperPath -and $WiperArgs) {
    $prof = @{ Exe = @(); Dirs = @(); Steps = @() }   # custom tool, filled in below
} else {
    throw "Unknown scenario '$Scenario'. Built-ins: $(($Profiles.Keys | Sort-Object) -join ', '). For another tool pass -WiperPath and -WiperArgs."
}

$isBuiltin = $prof.ContainsKey('Builtin')
$wiperExe  = $null
$steps     = @()
if (-not $isBuiltin) {
    $pf = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    $dirs = foreach ($d in $prof.Dirs) { foreach ($root in $pf) { Join-Path $root $d } }
    $wiperExe = Find-Tool $WiperPath $prof.Exe @($dirs | Where-Object { $_ })
    if (-not $wiperExe) { throw "Could not locate the wiper executable for '$Scenario'. Pass -WiperPath." }
    Write-Ok "Wiper -> $wiperExe"
    $steps = if ($WiperArgs) { @($WiperArgs | ForEach-Object { @{ Args = $_; Ok = $WiperOkExitCodes } }) } else { @($prof.Steps) }
    if (-not $steps.Count) { throw "No command line for '$Scenario'. Pass -WiperArgs." }
}
$waitProcs = @($WaitProcess) + $(if ($prof.ContainsKey('Wait')) { $prof.Wait } else { @() })

# Target volume must be NTFS and have a USN journal
if (-not (Test-Path -LiteralPath "$TargetDrive\")) { throw "Target drive $TargetDrive not found. Pass -TargetDir on an existing NTFS volume." }
$vol = Get-Volume -DriveLetter $TargetDrive.Substring(0, 1)
if ($vol.FileSystem -ne 'NTFS') { throw "$TargetDrive is $($vol.FileSystem), not NTFS." }
Write-Ok "Target volume $TargetDrive (NTFS); corpus = $TargetDir"
& fsutil usn queryjournal $TargetDrive *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Info "No USN journal on $TargetDrive; creating one"
    & fsutil usn createjournal m=33554432 a=4194304 $TargetDrive | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not create USN journal on $TargetDrive" }
}

# Idempotency: reset corpus and case folder
foreach ($d in $TargetDir, $CaseDir) {
    if (Test-Path -LiteralPath $d) {
        Write-Info "Removing previous $d"
        Remove-Item -LiteralPath $d -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $TargetDir, $CaseDir -Force | Out-Null
Write-Ok "Case directory: $CaseDir"

# ------------------------------------------------- Phase 1: corpus planting
Write-Phase 'Phase 1: Controlled corpus planting'

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$words = 'report budget quarterly invoice client project meeting forecast audit policy contract '+
         'summary review schedule revenue vendor payroll compliance incident analysis draft' -split ' '
$rng = [System.Random]::new()

function New-Text([int]$Bytes) {
    $sb = [Text.StringBuilder]::new()
    while ($sb.Length -lt $Bytes) {
        [void]$sb.Append($words[$rng.Next($words.Count)]).Append(' ')
        if ($rng.Next(12) -eq 0) { [void]$sb.Append("`n") }
    }
    $sb.ToString(0, $Bytes)
}
function New-RandomBytes([int]$n) { $b = New-Object byte[] $n; $rng.NextBytes($b); $b }

function Add-ZipEntry($zip, [string]$Name, $Content, [bool]$Compress = $true) {
    $lvl = if ($Compress) { [IO.Compression.CompressionLevel]::Optimal } else { [IO.Compression.CompressionLevel]::NoCompression }
    $e = $zip.CreateEntry($Name, $lvl)
    $s = $e.Open()
    try {
        $bytes = if ($Content -is [byte[]]) { $Content } else { [Text.Encoding]::UTF8.GetBytes([string]$Content) }
        $s.Write($bytes, 0, $bytes.Length)
    } finally { $s.Dispose() }
}

function New-OoxmlFile([string]$Path, [string]$Kind, [int]$Size) {
    $fs = [IO.File]::Open($Path, 'Create')
    $zip = [IO.Compression.ZipArchive]::new($fs, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $ct = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="bin" ContentType="application/octet-stream"/>'
        if ($Kind -eq 'docx') {
            $ct += '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
            $rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument'
            $main = 'word/document.xml'
            $body = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t xml:space="preserve">' + (New-Text 2048) + '</w:t></w:r></w:p></w:body></w:document>'
        } else {
            $ct += '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
            $rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument'
            $main = 'xl/workbook.xml'
            $body = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
            $sheetRows = (1..20 | ForEach-Object { '<row r="{0}"><c r="A{0}" t="inlineStr"><is><t>{1}</t></is></c><c r="B{0}"><v>{2}</v></c></row>' -f $_, $words[$rng.Next($words.Count)], $rng.Next(100000) }) -join ''
            Add-ZipEntry $zip 'xl/worksheets/sheet1.xml' ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + $sheetRows + '</sheetData></worksheet>')
            Add-ZipEntry $zip 'xl/_rels/workbook.xml.rels' '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        }
        Add-ZipEntry $zip '[Content_Types].xml' $ct
        Add-ZipEntry $zip '_rels/.rels' ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="' + $rel + '" Target="' + $main + '"/></Relationships>')
        Add-ZipEntry $zip $main $body
        # Incompressible padding so the file lands near the requested size
        $pad = [Math]::Max(0, $Size - 3500)
        if ($pad -gt 0) { Add-ZipEntry $zip 'customXml/padding.bin' (New-RandomBytes $pad) $false }
    } finally { $zip.Dispose(); $fs.Dispose() }
}

function New-PdfFile([string]$Path, [int]$Size) {
    $text = (New-Text 400) -replace "[`r`n]", ' '
    $head = "%PDF-1.4`n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj`n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj`n" +
            "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj`n" +
            "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj`n"
    $stream = "BT /F1 10 Tf 40 750 Td ($text) Tj ET"
    $content = "4 0 obj<</Length $($stream.Length)>>stream`n$stream`nendstream endobj`n"
    $tail = "trailer<</Root 1 0 R/Size 6>>`n%%EOF`n"
    $fixed = $head.Length + $content.Length + $tail.Length
    $padLen = [Math]::Max(0, $Size - $fixed - 8)
    $comment = '%' + ((New-Text $padLen) -replace "[`r`n]", ' ') + "`n"
    [IO.File]::WriteAllText($Path, $head + $content + $comment + $tail, [Text.Encoding]::ASCII)
}

$extensions = 'docx', 'xlsx', 'pdf', 'txt'
$truth = [System.Collections.Generic.List[object]]::new()

for ($i = 1; $i -le $FileCount; $i++) {
    $ext  = $extensions[($i - 1) % $extensions.Count]
    $size = $rng.Next(4KB, 500KB + 1)
    $name = '{0}_{1:D3}_{2}.{3}' -f $words[$rng.Next($words.Count)], $i, ([guid]::NewGuid().ToString('N').Substring(0, 6)), $ext
    $path = Join-Path $TargetDir $name
    switch ($ext) {
        'docx' { New-OoxmlFile $path 'docx' $size }
        'xlsx' { New-OoxmlFile $path 'xlsx' $size }
        'pdf'  { New-PdfFile $path $size }
        'txt'  { [IO.File]::WriteAllText($path, (New-Text $size), [Text.Encoding]::ASCII) }
    }
    $fi = Get-Item -LiteralPath $path
    $truth.Add([ordered]@{
        name          = $fi.Name
        path          = $fi.FullName
        extension     = $ext
        size_bytes    = $fi.Length
        sha256        = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        created_utc   = $fi.CreationTimeUtc.ToString('o')
        modified_utc  = $fi.LastWriteTimeUtc.ToString('o')
        accessed_utc  = $fi.LastAccessTimeUtc.ToString('o')
    })
    Write-Progress -Activity 'Planting corpus' -Status $name -PercentComplete (100 * $i / $FileCount)
}
Write-Progress -Activity 'Planting corpus' -Completed
Write-Ok "Planted $($truth.Count) files in $TargetDir"
Write-Info "Settling $SettleSeconds s for USN journal"
Start-Sleep -Seconds $SettleSeconds

# ---------------------------------------------------- Phase 2: wiping
Write-Phase "Phase 2: Wiping ($Scenario)"

$startUtc = [DateTime]::UtcNow
$wipeNotes = [System.Collections.Generic.List[string]]::new()
$wipeExit = $null

if ($isBuiltin) {
    Remove-Item -LiteralPath $TargetDir -Recurse -Force
    $wipeExit = 0
} else {
    if ($prof.ContainsKey('Pre')) { & $prof.Pre $wiperExe }
    $n = 0
    foreach ($s in $steps) {
        $n++
        $argLine = ([string]$s.Args).Replace('{TARGET}', $TargetDir).Replace('{DRIVE}', $TargetDrive)
        $wipeExit = Invoke-Exe $wiperExe $argLine "wiper_${Scenario}_step$n.log" @($s.Ok)
    }
    if ($waitProcs.Count) {
        $deadline = (Get-Date).AddMinutes(10)
        while ((Get-Process -Name $waitProcs -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
        }
    }
}

# Some wipers return before finishing (GUI/service hand-off): wait for the files to disappear.
if (-not $isBuiltin) {
    $deadline = (Get-Date).AddMinutes($WipeTimeoutMinutes)
    while ((Get-Date) -lt $deadline -and @($truth | Where-Object { Test-Path -LiteralPath $_.path }).Count -gt 0) {
        Start-Sleep -Seconds 2
    }
}

$endUtc = [DateTime]::UtcNow
Write-Ok ("Wipe window: {0} -> {1} ({2:N1}s, exit {3})" -f $startUtc.ToString('o'), $endUtc.ToString('o'), ($endUtc - $startUtc).TotalSeconds, $wipeExit)

$remaining = @($truth | Where-Object { Test-Path -LiteralPath $_.path })
foreach ($t in $truth) { $t['exists_after_wipe'] = [bool](Test-Path -LiteralPath $t.path) }
if ($remaining.Count -gt 0) {
    $msg = "$($remaining.Count)/$($truth.Count) target files still exist after '$Scenario'. Ground-truth label would be invalid."
    if ($StrictWipeCheck) { throw $msg }
    Write-Warn2 $msg
    $wipeNotes.Add($msg)
} else {
    Write-Ok 'All target files removed'
}

Write-Info "Settling $SettleSeconds s before parsing"
Start-Sleep -Seconds $SettleSeconds

# ------------------------------------------------- Phase 3: artifact parsing
Write-Phase 'Phase 3: Artifact parsing (Zimmerman tools)'

$parseResults = [ordered]@{}
function Invoke-Parse([string]$Key, [scriptblock]$Action) {
    try { & $Action; $parseResults[$Key] = 'ok'; Write-Ok "$Key parsed" }
    catch { $parseResults[$Key] = "failed: $($_.Exception.Message)"; Write-Warn2 "$Key failed: $($_.Exception.Message)" }
}

$mftecmd = $ez['MFTECmd.exe']

# $MFT and $J come from the TARGET volume; Prefetch/Amcache come from the system volume (C:)
Invoke-Parse 'mft' {
    Invoke-Exe $mftecmd "-f `"$TargetDrive\`$MFT`" --csv `"$CaseDir`" --csvf `"mftecmd_`$MFT_Output.csv`"" 'mftecmd_mft.log' | Out-Null
}
Invoke-Parse 'usnjrnl' {
    Invoke-Exe $mftecmd "-f `"$TargetDrive\`$Extend\`$UsnJrnl:`$J`" -m `"$TargetDrive\`$MFT`" --csv `"$CaseDir`" --csvf `"mftecmd_`$J_Output.csv`"" 'mftecmd_j.log' | Out-Null
}
Invoke-Parse 'prefetch' {
    if (-not (Test-Path 'C:\Windows\Prefetch')) { throw 'C:\Windows\Prefetch missing (Prefetch disabled / SysMain stopped?)' }
    Invoke-Exe $ez['PECmd.exe'] "-d `"C:\Windows\Prefetch`" --csv `"$CaseDir`" --csvf `"pecmd_PECmd_Output.csv`"" 'pecmd.log' | Out-Null
}
Invoke-Parse 'amcache' {
    $hve = 'C:\Windows\appcompat\Programs\Amcache.hve'
    $src = $hve
    $tmp = Join-Path $CaseDir '_amcache_tmp'
    $shadowId = $null
    try {
        try {
            New-Item -ItemType Directory -Path $tmp -Force | Out-Null
            Copy-Item -LiteralPath $hve -Destination (Join-Path $tmp 'Amcache.hve') -ErrorAction Stop
        } catch {
            # Live hive is locked: copy it (and its transaction logs) from a VSS snapshot.
            Write-Info 'Amcache.hve locked; using a VSS snapshot'
            $r = (Get-WmiObject -List Win32_ShadowCopy).Create('C:\', 'ClientAccessible')
            if ($r.ReturnValue -ne 0) { throw "VSS snapshot failed (code $($r.ReturnValue))" }
            $shadowId = $r.ShadowID
            $sc = Get-WmiObject Win32_ShadowCopy | Where-Object { $_.ID -eq $shadowId }
            $link = Join-Path $env:TEMP ('wt_shadow_' + [guid]::NewGuid().ToString('N'))
            cmd /c mklink /d "`"$link`"" "`"$($sc.DeviceObject)\`"" | Out-Null
            try {
                Get-ChildItem -LiteralPath "$link\Windows\appcompat\Programs" -Filter 'Amcache.hve*' -Force |
                    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $tmp -Force }
            } finally { cmd /c rmdir "`"$link`"" | Out-Null }
        }
        Invoke-Exe $ez['AmcacheParser.exe'] "-f `"$(Join-Path $tmp 'Amcache.hve')`" --csv `"$CaseDir`" --csvf `"amcache.csv`" -i" 'amcacheparser.log' | Out-Null
    } finally {
        if ($shadowId) { (Get-WmiObject Win32_ShadowCopy | Where-Object { $_.ID -eq $shadowId }).Delete() }
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------- Phase 4: manifest & validation
Write-Phase 'Phase 4: Manifest and packaging'

$os = Get-CimInstance Win32_OperatingSystem
$manifest = [ordered]@{
    scenario        = $Scenario
    start_time      = $startUtc.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    end_time        = $endUtc.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    wiper_exe       = $wiperExe
    wiper_exit_code = $wipeExit
    wipe_notes      = @($wipeNotes)
    target_dir      = $TargetDir
    target_volume   = $TargetDrive
    file_count      = $truth.Count
    targeted_files  = @($truth)
    os_version      = [ordered]@{
        caption = $os.Caption
        version = $os.Version
        build   = $os.BuildNumber
        ubr     = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction SilentlyContinue).UBR
        arch    = $os.OSArchitecture
    }
    hostname        = $env:COMPUTERNAME
    parse_results   = $parseResults
    generated_utc   = [DateTime]::UtcNow.ToString('o')
}
$gtPath = Join-Path $CaseDir 'ground_truth.json'
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $gtPath -Encoding UTF8
Write-Ok "Wrote $gtPath"

# Verify CSV names match WiperTrace discovery patterns
$patterns = [ordered]@{
    usnjrnl  = '*mftecmd_$j_output*.csv'
    mft      = '*mftecmd_$mft_output*.csv'
    prefetch = '*pecmd_output.csv'
    amcache  = '*amcache*.csv'
}
$missing = @()
foreach ($k in $patterns.Keys) {
    $hit = Get-ChildItem -LiteralPath $CaseDir -Filter $patterns[$k] -File -ErrorAction SilentlyContinue
    if ($hit) { Write-Ok ("{0,-9} {1}" -f $k, ($hit.Name -join ', ')) }
    else      { Write-Warn2 ("{0,-9} no file matching {1}" -f $k, $patterns[$k]); $missing += $k }
}

Write-Host ''
if ($missing.Count -eq 0 -and $remaining.Count -eq 0) {
    Write-Host "Dataset complete: $CaseDir" -ForegroundColor Green
} else {
    Write-Host "Dataset generated with issues (missing: $($missing -join ', '); files remaining: $($remaining.Count)): $CaseDir" -ForegroundColor Yellow
    exit 2
}
