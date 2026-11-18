# WiperTrace User Manual

This manual takes you from a fresh Windows PC to a finished attribution report.
Work through it in order the first time; afterwards, use the
[command reference](#11-command-reference) and
[troubleshooting](#12-troubleshooting) sections as needed.

For what WiperTrace is and why it exists, see the [README](../README.md). For
how it works internally, see [ARCHITECTURE.md](ARCHITECTURE.md).

**Contents**

1. [Before you begin](#1-before-you-begin)
2. [Install WiperTrace](#2-install-wipertrace)
3. [Install the external tools](#3-install-the-external-tools)
4. [Check the installation](#4-check-the-installation)
5. [Analysing a real case](#5-analysing-a-real-case)
6. [Reading the report](#6-reading-the-report)
7. [Worked example](#7-worked-example)
8. [Alternative: extract and parse by hand](#8-alternative-extract-and-parse-by-hand)
9. [Creating your own test cases](#9-creating-your-own-test-cases)
10. [Advanced use](#10-advanced-use)
11. [Command reference](#11-command-reference)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Before you begin

### What you need

| Item | Why |
|---|---|
| Windows 10 or 11 PC | The parsers WiperTrace uses are Windows tools. PECmd can only decompress Windows 10/11 Prefetch on Windows. |
| Python 3.10 or newer | WiperTrace is a Python program. |
| .NET 10 runtime | Required by the Eric Zimmerman tools (MFTECmd, PECmd, AmcacheParser). |
| A disk image (`.E01`, `.dd`, `.raw`, `.img`) | The evidence. Section 5 explains how to make one. |
| A few GB of free disk space | For the extracted artifacts and CSVs. |

### How to type the commands

All commands are for **PowerShell**. In VS Code, open a terminal with
**Ctrl+`** (it opens PowerShell by default on Windows). Run the commands from
the repository root: the `dfwipertracer` folder that contains
`pyproject.toml`, not the inner `wipertrace` folder.

Lines ending in a backtick (`` ` ``) continue on the next line. Copy the whole
block.

### Why this manual writes `python -m …`

On some Windows 11 PCs (those with **Smart App Control** or another Application
Control policy) the small launcher programs that pip installs, such as
`pip.exe`, `pip3.exe` and `wipertrace.exe`, are blocked with:

```
Program 'wipertrace.exe' failed to run: An Application Control policy has blocked this file
```

Running the same program through Python always works, so this manual uses:

| Instead of | Type |
|---|---|
| `pip install …` | `python -m pip install …` |
| `wipertrace …` | `python -m wipertrace …` |
| `pytest` | `python -m pytest` |

If the short forms work on your PC, you can use them; the arguments are the
same.

---

## 2. Install WiperTrace

From the repository root:

```powershell
py -m venv .venv                          # use `py`; `python` may open the Microsoft Store
.\.venv\Scripts\Activate.ps1              # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"         # WiperTrace + test tools
python -m pip install -e ".[image]"       # needed for `collect` (reads raw/E01 images)
```

Your prompt now starts with `(.venv)`. **Activate the venv again in every new
terminal** before running WiperTrace:

```powershell
.\.venv\Scripts\Activate.ps1
```

To uninstall, delete the `.venv` and `wipertrace.egg-info` folders.

<details>
<summary>Linux / macOS (secondary)</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pip install -e ".[image]"   # optional; needs a C compiler and zlib headers
```

`demo`, `analyze`, `rules` and the tests work the same. The EZ tools' .NET
builds run on Linux, but PECmd cannot decompress Windows 10/11 Prefetch there,
so Prefetch evidence will be missing. Run the parsers on Windows and copy the
CSVs across, or accept the lower coverage. Continue lines with `\` instead of a
backtick and pass the EZ tools' `.dll` builds (they are run through `dotnet`).

</details>

---

## 3. Install the external tools

| Tool | Used for | Required? |
|---|---|---|
| MFTECmd, PECmd, AmcacheParser | Parsing $MFT, $J, Prefetch, Amcache | Yes |
| [LogFileParser](https://github.com/jschicht/LogFileParser) | Parsing $LogFile | Optional, raises coverage |
| [FTK Imager](https://www.exterro.com/ftk-product-downloads/ftk-imager) | Making E01 images | Only if you need to make an image |

### 3.1 Install the .NET 10 runtime

```powershell
winget install Microsoft.DotNet.Runtime.10
```

No winget? Download the ".NET Runtime 10" x64 installer from
<https://dotnet.microsoft.com/download/dotnet/10.0>. **Close and reopen the
terminal afterwards.**

### 3.2 Download the Eric Zimmerman tools

1. Download `Get-ZimmermanTools.zip` from <https://ericzimmerman.github.io/>
   and unzip it.
2. In the unzipped folder, run:

   ```powershell
   Unblock-File .\Get-ZimmermanTools.ps1
   powershell -ExecutionPolicy Bypass -File .\Get-ZimmermanTools.ps1 -Dest C:\Tools\ezt
   ```

   `Unblock-File` removes the "downloaded from the internet" mark;
   `-ExecutionPolicy Bypass` applies to this one run only. The script
   downloads every EZ tool (only three are needed) as .NET 10 builds.

3. Find where the three tools landed (often a `net` subfolder):

   ```powershell
   Get-ChildItem C:\Tools\ezt -Recurse -Filter MFTECmd.exe | Select-Object FullName
   ```

   Remember that folder: it is your `$ez` path in section 5. For example,
   `C:\Tools\ezt\net\MFTECmd.exe` → `$ez = "C:\Tools\ezt\net"`.

4. Check that the tools run:

   ```powershell
   & "C:\Tools\ezt\net\MFTECmd.exe" --version
   ```

   | Result | Meaning |
   |---|---|
   | A version number | Ready. |
   | `You must install or update .NET … version '10.0.0'` | .NET 10 is missing: repeat 3.1, then open a new terminal. |
   | `An Application Control policy has blocked this file` | Use the `.dll` builds instead: pass `"$ez\MFTECmd.dll"` etc. to `collect`; WiperTrace runs them through `dotnet`. |

### 3.3 (Optional) LogFileParser and FTK Imager

Download them from the links in the table above and unzip/install anywhere.
Their use is covered in section 5.

---

## 4. Check the installation

Run a synthetic case. No evidence or external tools are needed:

```powershell
python -m wipertrace demo sdelete --out demo_case
python -m wipertrace analyze demo_case --out demo_report
```

Expected output ends with:

```
ATTRIBUTED: Attributed to Sysinternals SDelete: support 1.00, coverage 1.00, confidence 1.00 (high).
...
artifacts: usnjrnl=present, mft=present, logfile=present, prefetch=present, amcache=missing
wrote demo_report\wipertrace_report.json
wrote demo_report\wipertrace_report.md
```

Open `demo_report\wipertrace_report.md` to see what a report looks like.

Other demo scenarios: `cipher`, `bleachbit6`, `bleachbit4`, `eraser`,
`ccleaner`, `bcwipe`, and the negative controls `explorer_delete` and
`rename_churn` (these should give `no_wiper_indicated`). Add
`--hard-shutdown` to simulate a power cut before the final journal records.

Optionally, run the test suite:

```powershell
python -m pytest
```

---

## 5. Analysing a real case

### Overview

```
 evidence (VM disk / physical drive)
      │  Step 1  acquire: FTK Imager → E01   (or raw .dd/.img)
      ▼
 image.E01
      │  Step 2  wipertrace collect   → extracts $MFT, $J, $LogFile, Prefetch, Amcache
      ▼                                 and runs MFTECmd / PECmd / AmcacheParser
 case folder of CSVs
      │  Step 3  (optional) parse $LogFile with LogFileParser
      │  Step 4  wipertrace analyze
      ▼
 wipertrace_report.md / .json
```

### Which disk holds which evidence

WiperTrace needs two kinds of evidence, and they may be on **different
volumes**:

| Evidence | Lives on | Proves |
|---|---|---|
| $UsnJrnl:$J, $MFT, $LogFile | **The volume the wiped files were on** | *How* the files were destroyed (rename patterns, overwrites) |
| Prefetch, Amcache | **The Windows (system) volume**, usually C: | *Which program ran*, and when |

* Wiped files were on C: → you need **one image** (the system disk).
* Wiped files were on another disk/partition (D:, a USB stick, a second
  virtual disk) → you need **both** that disk and the system disk. An image of
  only the data disk has no Prefetch/Amcache; an image of only the system disk
  has no journal records of the wipe.

Not sure where the wipe happened? Image the system disk, run Step 2, and look
at the wiper's Prefetch entry: PECmd's `FilesLoaded` column lists the files the
tool touched (e.g. `\VOLUME{…}\WIPETEST\SECRET.TXT`).

### Step 0: set your paths

Every command below uses these variables. Change them to match your machine
and run them **in the same terminal** you use for the later steps (variables
are lost when the terminal closes):

```powershell
.\.venv\Scripts\Activate.ps1
$ez    = "C:\Tools\ezt\net"             # folder containing MFTECmd.exe, PECmd.exe, AmcacheParser.exe (section 3.2)
$work  = "C:\Cases\case01"              # where images, CSVs and reports go
$image = "$work\sys.E01"                # the image you will analyse
New-Item -ItemType Directory -Force $work | Out-Null
Test-Path "$ez\MFTECmd.exe", "$ez\PECmd.exe", "$ez\AmcacheParser.exe"   # all three must be True
```

If any `Test-Path` result is `False`, fix `$ez` before continuing.

### Step 1: acquire an image

Skip this step if you already have an `.E01` or raw image.

Always capture with the machine **off**, and never run imaging tools inside the
machine you are investigating: they create their own Prefetch and journal
records.

#### 1a. VMware Workstation VM

1. Shut down or power off the VM (not *Suspend*).
2. Find the **current** disk file: *VM → Settings → Hard Disk N → Disk file*.
   If the VM has snapshots, this is a file like `Windows 11 x64-000002.vmdk`.
   The base `.vmdk` only holds the state from **before** the first snapshot;
   imaging it misses everything that happened after (including the wipe).
3. Merge the snapshot chain into one standalone disk. The destination folder
   must already exist:

   ```powershell
   $vdm = "C:\Program Files\VMware\VMware Workstation\vmware-vdiskmanager.exe"   # or "Program Files (x86)" on older installs
   & $vdm -r "<Disk file from step 2>" -t 0 "$work\sys.vmdk"
   ```

   Repeat for each disk you need (e.g. `data.vmdk`). This only reads the VM's
   files.
4. Image the `.vmdk` with FTK Imager (1d).

#### 1b. VirtualBox VM

1. Power the VM off.
2. Clone the current disk (cloning the newest snapshot's disk merges the chain).
   Raw output can go straight to Step 2 without FTK Imager:

   ```powershell
   & "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" clonemedium disk "<current .vdi path or UUID>" "$work\sys.raw" --format RAW
   ```

   Find the current disk under *File → Tools → Media* (the innermost child
   under the VM's disk). Use `--format VHD` instead if you want an E01 via FTK
   Imager.

#### 1c. Physical drive or USB stick

Connect it through a write blocker and image it with FTK Imager using
*Source: Physical Drive* (1d, step 1).

#### 1d. Create the E01 in FTK Imager

1. *File → Create Disk Image → Image File* (for a `.vmdk`/`.vhd`) or
   *Physical Drive*, and select the source.
2. *Add… → E01*. Fill in the evidence details. Use **Notes** for ground truth
   (tool, version, command, UTC time, clean or hard shutdown).
3. Choose the folder and file name; set **Image Fragment Size = 0** (single file).
4. Tick **Verify images after they are created** → **Start**. Keep the `.E01`
   and its `.E01.txt` hash log.

Other formats: any raw image (`.dd`, `.raw`, `.img`) works directly in Step 2.
VMDK, VDI and VHD files do not; convert them first as above.

### Step 2: extract and parse (`collect`)

Use one **new, empty** `--out` folder per image:

```powershell
python -m wipertrace collect $image --out "$work\sys" `
    --mftecmd "$ez\MFTECmd.exe" --pecmd "$ez\PECmd.exe" --amcacheparser "$ez\AmcacheParser.exe"
```

`collect` reads the image read-only, copies the artifacts to `<out>\raw\`,
records their SHA-256 in `<out>\acquisition_manifest.json`, and writes the CSVs
to `<out>`:

```
sys\
├── raw\  $MFT  $J  $LogFile  Prefetch\*.pf  Amcache\Amcache.hve(.LOG1/.LOG2)
├── acquisition_manifest.json
├── mftecmd_$MFT_Output.csv
├── mftecmd_$J_Output.csv
├── pecmd_PECmd_Output.csv
└── amcache_*FileEntries.csv  (+ other AmcacheParser tables)
```

**Check the warnings.** Which ones are expected depends on the disk:

| Warning | System disk | Data-only disk |
|---|---|---|
| `not found in image: …Amcache.hve` | Problem: wrong volume (see below) | Expected |
| `Prefetch folder not found on NTFS volume N` | Problem: wrong volume, or Prefetch disabled | Expected |
| `not found in image: /$Extend/$UsnJrnl:$J` | Journal disabled or deleted; analysis loses its strongest rules | Same |

**Choosing the volume.** A disk image usually has several partitions; `collect`
uses the first NTFS one (`--volume 0`). On many Windows installs that is the
Recovery partition, not C:. If Prefetch/Amcache are reported missing on a
system disk, the warning lists the other volume numbers. Re-run with
`--volume 1`, `--volume 2`, … into a **fresh** `--out` folder until they are
found:

```powershell
python -m wipertrace collect $image --out "$work\sys_v1" --volume 1 `
    --mftecmd "$ez\MFTECmd.exe" --pecmd "$ez\PECmd.exe" --amcacheparser "$ez\AmcacheParser.exe"
```

Then use that folder in Step 4. A data disk normally has just one NTFS volume.

For a **data disk**, Prefetch and Amcache are not needed, so `--mftecmd` alone
is enough:

```powershell
python -m wipertrace collect "$work\data.E01" --out "$work\data" --mftecmd "$ez\MFTECmd.exe"
```

### Step 3 (optional): parse $LogFile

No EZ tool parses $LogFile, so `collect` only extracts it to
`<out>\raw\$LogFile`. Without it the $LogFile rules show as *n/a* and coverage
drops (for SDelete, max confidence ≈ 0.80 instead of 1.00), but attribution
still works.

1. Run LogFileParser and give it `<out>\raw\$LogFile` as input.
2. It writes several CSVs. Copy **only the main one** (columns such as
   `lf_LSN`, `lf_RedoOperation`, `lf_FileName`) into the case folder as
   `logfile.csv`. Any CSV with `logfile` in its name is picked up, so leave the
   others out of the case folder.
3. Using a different parser? Map its columns with `--columns`
   (section 10.2).

### Step 4: analyse

**Everything in one case folder** (wiped files were on the system disk):

```powershell
python -m wipertrace analyze "$work\sys" --out "$work\report"
```

**Two images** (wiped files on a data disk): analyse the data-disk folder and
add the execution evidence from the system-disk folder:

```powershell
python -m wipertrace analyze "$work\data" `
    --prefetch "$work\sys\pecmd_PECmd_Output.csv" `
    --amcache  (Get-ChildItem "$work\sys\*FileEntries*.csv") `
    --out "$work\report"
```

**Any CSVs from anywhere**: point at each one; no case folder needed. Every
`--<artifact>` option accepts one or more files and can be repeated:

```powershell
python -m wipertrace analyze --usnjrnl X:\a\usn.csv --mft X:\a\mft.csv --prefetch Y:\b\pf.csv --out "$work\report"
```

Explicit files replace discovery for that artifact only. For time windows and
other options, see section 10.

---

## 6. Reading the report

### Check the artifact line first

The console summary ends with an artifact line. Check it before trusting the
verdict:

```
artifacts: usnjrnl=present, mft=present, logfile=missing, prefetch=present, amcache=empty
```

| State | Meaning |
|---|---|
| `present` | Parsed, contains records |
| `missing` | No CSV supplied or found; its rules are *n/a* and coverage drops |
| `empty` | CSV found but no usable rows (header only, wrong file, or blank paths) |

### What the report contains

The report (`wipertrace_report.md`, same data in `.json`) contains:

* **Conclusion**: one of

  | Conclusion | Meaning |
  |---|---|
  | `attributed` | One tool clearly fits best |
  | `ambiguous` | Two or more tools fit equally well (they are named) |
  | `no_wiper_indicated` | No tool's signature matched enough |
  | `insufficient_artifacts` | Too little evidence was present to decide |

* **Support**: share of the tool's *evaluable* signature that matched.
* **Coverage**: share of the tool's signature that could be evaluated with the
  artifacts present. Low coverage = the verdict rests on few artifacts.
* **Confidence**: support × √coverage (≥ 0.75 high, ≥ 0.5 medium).
* **Rule results**: every rule as MATCH / – / n/a, with evidence rows
  (`file.csv:row`) you can open in Timeline Explorer to see the raw record.
* **Limitations** and **input manifest** (SHA-256 of every input).

Rule IDs tell you what kind of evidence matched:

| Rule ID part | Evidence |
|---|---|
| `-USN-` | $UsnJrnl (journal) pattern |
| `-LOG-` | $LogFile pattern |
| `-MFT-` | $MFT residue |
| `-PF-` | Execution evidence (Prefetch or Amcache) |
| `-COR-` | Execution and journal pattern close together in time |

### How much to trust it

* **Strongest**: a journal rule (`*-USN-*`) **and** the execution rule
  (`*-PF-*`) **and** the correlation rule (`*-COR-*`) all match. The tool ran,
  and the wipe pattern appeared within minutes of it.
* **Treat with caution**: a tool listed under *"Also consistent"* whose
  `*-PF-*` rule did not match. On busy system volumes, routine Windows
  activity (log rotation, temp-file renames) can satisfy some BleachBit and
  Eraser journal rules. Confirm with the Prefetch evidence or the time window.
* **CCleaner**: it also runs for ordinary cleaning. If only `CCL-PF-001`
  matches, CCleaner ran, but that does not show it securely deleted anything.
* **`no_wiper_indicated` when you expected a hit**: check that you analysed the
  journals of the volume the files were wiped on, and that the time window
  (if set) covers the wipe.

---

## 7. Worked example

A system-disk image `CCleaner-evidence.E01` sits in the Downloads folder, and
the EZ tools are in `C:\Tools\ezt\net`.

```powershell
# Step 0
.\.venv\Scripts\Activate.ps1
$ez    = "C:\Tools\ezt\net"
$work  = "C:\Cases\ccleaner"
$image = "$env:USERPROFILE\Downloads\CCleaner-evidence.E01"
New-Item -ItemType Directory -Force $work | Out-Null
Test-Path "$ez\MFTECmd.exe", "$ez\PECmd.exe", "$ez\AmcacheParser.exe"   # True True True

# Step 2
python -m wipertrace collect $image --out "$work\sys" `
    --mftecmd "$ez\MFTECmd.exe" --pecmd "$ez\PECmd.exe" --amcacheparser "$ez\AmcacheParser.exe"
# If it warns that Prefetch/Amcache are missing: re-run with --volume 1 into "$work\sys_v1"

# Step 4
python -m wipertrace analyze "$work\sys" --out "$work\report"

# Read the report
notepad "$work\report\wipertrace_report.md"
```

When you read the result:

1. Check the `artifacts:` line. You want `usnjrnl`, `mft` and `prefetch` to
   be `present`.
2. Look for `CCL-USN-*` matches (the secure-deletion pattern) together with
   `CCL-PF-001` (CCleaner ran) and the `-COR-` rule.

---

## 8. Alternative: extract and parse by hand

Use this if `collect` cannot read your image, or you already have exported
artifacts (e.g. from FTK Imager *Export Files* or KAPE).

1. Export these from the volume(s):
   `$MFT`, `$LogFile`, `$Extend\$UsnJrnl:$J` (the `$J` stream),
   `Windows\Prefetch\*.pf`, `Windows\appcompat\Programs\Amcache.hve` (+ `.LOG1`, `.LOG2`
   in the same folder).
2. Parse them into one folder with names WiperTrace recognises:

   ```powershell
   $raw = "$work\export"; $out = "$work\manual"
   & "$ez\MFTECmd.exe" -f "$raw\`$MFT" --csv $out --csvf mft.csv
   & "$ez\MFTECmd.exe" -f "$raw\`$J" -m "$raw\`$MFT" --csv $out --csvf usnjrnl.csv
   & "$ez\PECmd.exe" -d "$raw\Prefetch" --csv $out --csvf prefetch.csv
   & "$ez\AmcacheParser.exe" -f "$raw\Amcache.hve" -i --csv $out --csvf amcache.csv
   python -m wipertrace analyze $out --out "$work\report"
   ```

   Note the backtick in `` `$MFT `` / `` `$J ``: without it PowerShell treats
   `$MFT` as a variable.

Discovery rules (case-insensitive; give files explicitly if yours differ):

| Artifact | Recognised file names |
|---|---|
| $UsnJrnl:$J | `*$J_Output*.csv` (MFTECmd default), `*usnjrnl*.csv`, `usn*.csv` |
| $MFT | `*$MFT_Output*.csv` (MFTECmd default), `mft.csv` |
| $LogFile | `*logfile*.csv` |
| Prefetch | `*PECmd_Output.csv`, `prefetch.csv` |
| Amcache | `*FileEntries*.csv` (AmcacheParser), `amcache.csv` |

---

## 9. Creating your own test cases

To measure accuracy on real data, generate cases in a **virtual machine**.
Wipers destroy data for real, and running them on your own PC also fills its
Prefetch and journals with test noise.

1. Snapshot a clean Windows VM; restore it before each run so each case holds
   one tool.
2. Check Prefetch is enabled in the guest: `EnablePrefetcher` = 3 under
   `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters`.
3. Put test files on **C:** if you want a single image. A separate data disk
   keeps the journals cleaner but means imaging two disks.
4. Record ground truth: tool, version, exact command, start/end time in UTC
   (`(Get-Date).ToUniversalTime()`).
5. Example (SDelete), run **inside the VM**:

   ```powershell
   Invoke-WebRequest https://download.sysinternals.com/files/SDelete.zip -OutFile $env:TEMP\SDelete.zip
   Expand-Archive $env:TEMP\SDelete.zip C:\Tools\SDelete
   New-Item -ItemType Directory C:\WipeTest | Out-Null
   Set-Content C:\WipeTest\secret.txt ("confidential data " * 5000)    # > 1 KB, so not MFT-resident
   (Get-Date).ToUniversalTime()
   C:\Tools\SDelete\sdelete64.exe -accepteula -p 1 C:\WipeTest\secret.txt
   ```

6. Wait at least a minute (Windows writes Prefetch about 10 s after launch),
   then **shut down** (clean case) or **power off** (hard-shutdown case).
7. Continue with Step 1 of section 5.

---

## 10. Advanced use

### 10.1 Narrowing and testing the analysis

| Option | Purpose |
|---|---|
| `--since "2026-10-14 08:00:00"` `--until …` | Only events in this **UTC** window (cuts noise on busy disks) |
| `--drop logfile` | Ignore an artifact class (ablation / robustness tests); repeatable |
| `--format json` | Only write the JSON report (default `json,md`) |
| `--quiet` | Do not print the console summary |

### 10.2 Mapping different CSV headers

If your parser names columns differently, write a YAML file and pass it with
`--columns cols.yaml`. Your names are tried before the defaults:

```yaml
logfile:
  redo_op: RedoOperation
  file_ref: TargetMftRef
  name: FileName
```

Logical column names for each artifact are listed in `wipertrace/config.py`.

### 10.3 Adding or tuning a signature

Rules live in `wipertrace/signatures/default.yaml`. A new tool usually needs no
Python. Combine the four matcher kinds:

```yaml
- id: NEW-USN-001
  tool: newtool                 # declare it under `tools:` first
  description: Three renames to 8-char hex names
  requires: [usnjrnl]           # "prefetch|amcache" means either one
  weight: 4
  calibrated: false             # provisional threshold
  kind: rename_chain            # | per_file_count | match | correlate
  params: {min_renames: 3, max_renames: 3, name_regex: '^[0-9A-F]{8}$'}
```

Check it with `python -m wipertrace rules`, which validates the whole file and
lists every rule. Parameters for each kind are documented in
`wipertrace/engine/matchers.py`. Use your own file with
`analyze --signatures my.yaml`.

---

## 11. Command reference

Global options: `--version`, `-v/--verbose`.

### `demo`: write a synthetic case

```
python -m wipertrace demo SCENARIO --out DIR [--files N] [--hard-shutdown] [--seed N]
```

| Option | Default | Meaning |
|---|---|---|
| `SCENARIO` | | `sdelete`, `cipher`, `bleachbit6`, `bleachbit4`, `eraser`, `ccleaner`, `bcwipe`, `explorer_delete`, `rename_churn` |
| `--out` | required | Folder to write the case to |
| `--files` | 10 | Number of wiped files |
| `--hard-shutdown` | off | Simulate a power cut before the final records |
| `--seed` | 1 | Random seed |

### `collect`: extract artifacts from an image

```
python -m wipertrace collect IMAGE --out DIR [--volume N] [--mftecmd PATH] [--pecmd PATH] [--amcacheparser PATH]
```

| Option | Default | Meaning |
|---|---|---|
| `IMAGE` | | `.E01`, `.dd`, `.raw` or `.img` |
| `--out` | required | New, empty case folder |
| `--volume` | 0 | Index of the NTFS volume to read |
| `--mftecmd`, `--pecmd`, `--amcacheparser` | | Path to the tool's `.exe` or `.dll`; a tool left out is not run |

### `analyze`: attribute wiping activity

```
python -m wipertrace analyze [CASE_DIR] [--usnjrnl CSV…] [--mft CSV…] [--logfile CSV…]
        [--prefetch CSV…] [--amcache CSV…] [--drop ARTIFACT] [--since T] [--until T]
        [--signatures YAML] [--columns YAML] [--out DIR] [--format json,md] [--quiet]
```

| Option | Default | Meaning |
|---|---|---|
| `CASE_DIR` | | Folder of parser CSVs (optional if every CSV is given explicitly) |
| `--usnjrnl` … `--amcache` | discovered | One or more CSVs for that artifact; repeatable |
| `--drop` | | Ignore an artifact class; repeatable |
| `--since`, `--until` | | UTC window, `YYYY-MM-DD HH:MM:SS` |
| `--signatures` | bundled | Signature database YAML |
| `--columns` | | CSV column-name overrides |
| `--out` | `wipertrace_out` | Report folder |
| `--format` | `json,md` | Report formats |
| `--quiet` | off | No console summary |

### `rules`: validate and list the signature database

```
python -m wipertrace rules [--signatures YAML]
```

---

## 12. Troubleshooting

### Setup

| Symptom | Cause and fix |
|---|---|
| `An Application Control policy has blocked this file` (for `pip.exe`, `pip3.exe`, `wipertrace.exe`, `pytest.exe`) | Smart App Control blocks the venv's launcher programs. Use `python -m pip`, `python -m wipertrace`, `python -m pytest`. |
| `Python was not found; run without arguments to install from the Microsoft Store` | Use `py` instead of `python` to create the venv, or install Python and tick *Add to PATH*. |
| `No module named install` | You typed `python -m install …`. The command is `python -m pip install …`. |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. |
| `…\wipertrace does not appear to be a Python project` | You are in the inner `wipertrace` folder. `cd ..` to the folder with `pyproject.toml`. |
| `wipertrace : The term 'wipertrace' is not recognized` | The venv is not active in this terminal: `.\.venv\Scripts\Activate.ps1`. |
| `dotnet : The term 'dotnet' is not recognized` | .NET is not installed, or the terminal was opened before installing it. Section 3.1, then open a new terminal. |
| `You must install or update .NET to run this application … version '10.0.0'` | The EZ tools need .NET 10; an older version is installed. Section 3.1. |
| `pytsk3 is not installed` / `E01 support needs libewf-python` | `python -m pip install -e ".[image]"`. |

### Running `collect` and `analyze`

| Symptom | Cause and fix |
|---|---|
| `Test-Path` prints `False` / `error: --mftecmd … does not exist` | `$ez` points at the wrong folder. Find the tools with `Get-ChildItem C:\Tools\ezt -Recurse -Filter MFTECmd.exe` and set `$ez` to that folder (often `…\net`). |
| `error: Case directory not found` | The `collect` step before it failed, so the folder was never created. Fix the first error and re-run `collect`. |
| `No NTFS volume found in image` | The image is encrypted: BitLocker inside the guest (`manage-bde -status`), or the VM's disks are encrypted (VMware: *Settings → Options → Access Control*; choose encrypting only the TPM files). Or the image is of the wrong disk. |
| Prefetch / Amcache not found | Wrong `--volume`, or a data-only disk (expected). See Step 2. |
| `vmware-vdiskmanager`: `Failed to convert disk: A file was not found (0x4)` | The destination folder does not exist, or the source file name is wrong. Create the folder; copy the name from *VM Settings → Hard Disk → Disk file*. |
| Image shows files as they were before the test | You imaged the base `.vmdk`/`.vdi` of a VM with snapshots. Merge the chain first (Step 1a/1b). |
| `analyze` finds no wipe, but Prefetch shows the wiper ran | You analysed the journals of the wrong volume. Use the volume the wiped files were on (Prefetch `FilesLoaded` tells you which). |
| `amcache=empty` although `*FileEntries*.csv` has rows | AmcacheParser wrote rows with blank `FullPath`/`Name` (seen with Windows 11 hives). Amcache is also updated by a scheduled task and often lacks short-lived tools. Prefetch covers the same rule. |
| `not a recognised <artifact> CSV — missing columns` | Your parser uses different header names. Use `--columns` (section 10.2). |
| `unrecognized arguments` with `(Get-ChildItem …)` | Update to the current version; older versions accepted only one file per option. |
