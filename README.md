# WiperTrace

WiperTrace answers a second-order forensic question: **not "what was deleted?"
but "which wiping tool did the deleting, and when?"** It reads the NTFS journals
($UsnJrnl, $LogFile), $MFT residue and Windows execution artifacts (Prefetch,
Amcache), scores the evidence against a database of per-tool signatures, and
reports a ranked, confidence-scored attribution with the CSV row behind every
conclusion.

Supported tools (signature DB 0.1.0): SDelete 2.05, cipher /w, BleachBit 6.x,
BleachBit 4.x, Eraser 6.2. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the design.

**Contents:** [Install](#install) · [Quick start](#quick-start-no-evidence-needed) ·
[Tutorial: analysing a real case](#tutorial-analysing-a-real-case) ·
[Troubleshooting](#troubleshooting) · [Creating test cases](#creating-your-own-test-cases) ·
[Mapping CSV headers](#mapping-different-csv-headers) ·
[Signatures](#adding-or-tuning-a-signature) · [Tests](#tests) · [Limitations](#limitations)

## Install

Windows is the recommended platform. The parsers WiperTrace relies on are
Windows tools, and PECmd can only decompress Windows 10/11 Prefetch files on
Windows.

### Windows (recommended)

Requires Python 3.10+. In PowerShell, from the repository root (the
`dfwipertracer` folder that contains `pyproject.toml`, not the inner
`wipertrace` package folder):

```powershell
py -m venv .venv                   # use `py`; `python` may open the Microsoft Store
.\.venv\Scripts\Activate.ps1       # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev]"            # core + tests
pip install -e ".[image]"          # needed for `wipertrace collect` (reads raw/E01 images)
```

Everything is installed into `.venv`. To uninstall, delete `.venv` and
`wipertrace.egg-info`. Activate the venv again (`.\.venv\Scripts\Activate.ps1`)
in every new terminal before running `wipertrace`.

External tools (install separately, any location):

| Tool | Used for | Notes |
|---|---|---|
| [MFTECmd, PECmd, AmcacheParser](https://ericzimmerman.github.io/) | Parsing $MFT, $J, Prefetch, Amcache | Portable; unzip anywhere. Needs the .NET runtime named on the download page. `Get-ZimmermanTools.ps1` puts them in a `net9` (or similar) subfolder. |
| [LogFileParser](https://github.com/jschicht/LogFileParser) | Parsing $LogFile | Optional but recommended (raises coverage). Default column names assume its output. |
| [FTK Imager](https://www.exterro.com/ftk-product-downloads/ftk-imager) | Making E01 images | Or any tool that produces E01 or raw (`.dd`/`.raw`/`.img`). |

### Linux / macOS (secondary)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[image]"          # optional; needs a C compiler and zlib headers
```

`demo`, `analyze`, `rules` and the tests work the same on Linux and macOS. The
limitation is the parsers: the EZ tools' .NET builds run on Linux, but PECmd
cannot decompress Windows 10/11 Prefetch there, so Prefetch evidence will be
missing. Run the parsers on Windows and copy the CSVs across, or accept the
lower coverage. In the commands below, continue lines with `\` instead of a
backtick and pass the EZ tools' `.dll` builds (they are run through `dotnet`).

## Quick start (no evidence needed)

Run this first to confirm the install works:

```powershell
wipertrace demo sdelete --out demo_case          # synthetic SDelete case
wipertrace analyze demo_case --out demo_report   # -> wipertrace_report.json + .md
```

Expected: `ATTRIBUTED: Attributed to Sysinternals SDelete ... confidence 1.00`.

Scenarios: `sdelete`, `cipher`, `bleachbit6`, `bleachbit4`, `eraser`, and the
negative controls `explorer_delete`, `rename_churn`. Add `--hard-shutdown` to
simulate a power cut before the final journal records.

## Tutorial: analysing a real case

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

### Before you start: which disk holds which evidence

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
and run them in the same PowerShell window:

```powershell
$ez    = "C:\Tools\ezt\net9"            # folder containing MFTECmd.exe, PECmd.exe, AmcacheParser.exe
$work  = "D:\Cases\case01"              # where images, CSVs and reports go
$image = "$work\sys.E01"                # the image you will analyse
New-Item -ItemType Directory -Force $work | Out-Null
Test-Path "$ez\MFTECmd.exe", "$ez\PECmd.exe", "$ez\AmcacheParser.exe"   # all three must be True
```

Can't find the EZ tools? `Get-ChildItem C:\ -Recurse -Filter MFTECmd.exe -ErrorAction SilentlyContinue`.

### Step 1: acquire an image

Pick the section matching your evidence. Always capture with the machine **off**
and never run imaging tools inside the machine you are investigating: they
create their own Prefetch and journal records.

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
4. Image the `.vmdk` with FTK Imager (section 1d).

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
*Source: Physical Drive* (section 1d, step 1).

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

### Step 2: extract and parse (`wipertrace collect`)

Use one **new, empty** `--out` folder per image:

```powershell
wipertrace collect $image --out "$work\sys" `
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
system disk, the warning lists the other volume numbers; re-run with
`--volume 1`, `--volume 2`, … into a fresh `--out` folder until they are found.
A data disk normally has just one NTFS volume.

For a **data disk**, Prefetch and Amcache are not needed, so `--mftecmd` alone
is enough:

```powershell
wipertrace collect "$work\data.E01" --out "$work\data" --mftecmd "$ez\MFTECmd.exe"
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
   (see [Mapping different CSV headers](#mapping-different-csv-headers)).

### Step 4: analyse

**Everything in one case folder** (wiped files were on the system disk):

```powershell
wipertrace analyze "$work\sys" --out "$work\report"
```

**Two images** (wiped files on a data disk): analyse the data-disk folder and
add the execution evidence from the system-disk folder:

```powershell
wipertrace analyze "$work\data" `
    --prefetch "$work\sys\pecmd_PECmd_Output.csv" `
    --amcache  (Get-ChildItem "$work\sys\*FileEntries*.csv") `
    --out "$work\report"
```

**Any CSVs from anywhere**: point at each one; no case folder needed. Every
`--<artifact>` option accepts one or more files and can be repeated:

```powershell
wipertrace analyze --usnjrnl X:\a\usn.csv --mft X:\a\mft.csv --prefetch Y:\b\pf.csv --out "$work\report"
```

Explicit files replace discovery for that artifact only. Other options:

| Option | Purpose |
|---|---|
| `--since "2026-10-14 08:00:00"` `--until …` | Only events in this **UTC** window (cuts noise on busy disks) |
| `--drop logfile` | Ignore an artifact class (ablation / robustness tests) |
| `--columns cols.yaml` | Map non-default CSV header names |
| `--signatures my.yaml` | Use another signature database |
| `--format json` | Only write the JSON report |

### Step 5: check and read the result

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

The report (`wipertrace_report.md`, same data in `.json`) contains:

* **Conclusion**: `attributed`, `ambiguous` (names the tied tools),
  `no_wiper_indicated`, or `insufficient_artifacts`.
* **Support**: share of the tool's *evaluable* signature that matched.
* **Coverage**: share of the tool's signature that could be evaluated with the
  artifacts present. Low coverage = the verdict rests on few artifacts.
* **Confidence**: support × √coverage (≥ 0.75 high, ≥ 0.5 medium).
* **Rule results**: every rule as MATCH / – / n/a, with evidence rows
  (`file.csv:row`) you can open in Timeline Explorer.
* **Limitations** and **input manifest** (SHA-256 of every input).

How much to trust it:

* **Strongest**: a journal rule (`*-USN-*`) **and** the execution rule
  (`*-PF-*`) **and** the correlation rule (`*-COR-*`) all match. The tool ran,
  and the wipe pattern appeared within minutes of it.
* **Treat with caution**: a tool listed under *"Also consistent"* whose
  `*-PF-*` rule did not match. On busy system volumes, routine Windows
  activity (log rotation, temp-file renames) can satisfy some BleachBit and
  Eraser journal rules. Confirm with the Prefetch evidence or the time window.
* **`no_wiper_indicated` when you expected a hit**: check that you analysed the
  journals of the volume the files were wiped on, and that the time window
  (if set) covers the wipe.

### Alternative: extract and parse by hand

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
   wipertrace analyze $out --out "$work\report"
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

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Python was not found; run without arguments to install from the Microsoft Store` | Use `py` instead of `python`, or install Python and tick *Add to PATH*. |
| `…\wipertrace does not appear to be a Python project` | You are in the inner `wipertrace` folder. `cd ..` to the folder with `pyproject.toml`. |
| `wipertrace : The term 'wipertrace' is not recognized` | The venv is not active in this terminal: `.\.venv\Scripts\Activate.ps1`. |
| `error: --mftecmd … does not exist` | Wrong EZ tool path. Check with `Test-Path` (Step 0). |
| `pytsk3 is not installed` / `E01 support needs libewf-python` | `pip install -e ".[image]"`. |
| `No NTFS volume found in image` | The image is encrypted: BitLocker inside the guest (`manage-bde -status`), or the VM's disks are encrypted (VMware: *Settings → Options → Access Control*; choose encrypting only the TPM files). Or the image is of the wrong disk. |
| Prefetch / Amcache not found | Wrong `--volume`, or a data-only disk (expected). See Step 2. |
| `vmware-vdiskmanager`: `Failed to convert disk: A file was not found (0x4)` | The destination folder does not exist, or the source file name is wrong. Create the folder; copy the name from *VM Settings → Hard Disk → Disk file*. |
| Image shows files as they were before the test | You imaged the base `.vmdk`/`.vdi` of a VM with snapshots. Merge the chain first (Step 1a/1b). |
| `analyze` finds no wipe, but Prefetch shows the wiper ran | You analysed the journals of the wrong volume. Use the volume the wiped files were on (Prefetch `FilesLoaded` tells you which). |
| `amcache=empty` although `*FileEntries*.csv` has rows | AmcacheParser wrote rows with blank `FullPath`/`Name` (seen with Windows 11 hives). Amcache is also updated by a scheduled task and often lacks short-lived tools. Prefetch covers the same rule. |
| `not a recognised <artifact> CSV — missing columns` | Your parser uses different header names. Use `--columns` (below). |
| `unrecognized arguments` with `(Get-ChildItem …)` | Update to the current version; older versions accepted only one file per option. |

## Creating your own test cases

To measure accuracy on real data, generate cases in a **virtual machine**.
Wipers destroy data for real, and running them on your own PC also fills its
Prefetch and journals with test noise.

1. Snapshot a clean Windows VM; restore it before each run so each case holds
   one tool.
2. Check Prefetch is enabled in the guest: `EnablePrefetcher` = 3 under
   `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters`.
3. Put test files on **C:** if you want a single image (Step 1). A separate
   data disk keeps the journals cleaner but means imaging two disks.
4. Record ground truth: tool, version, exact command, start/end time in UTC
   (`(Get-Date).ToUniversalTime()`).
5. Example (SDelete):

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
7. Continue with Step 1 of the tutorial.

## Mapping different CSV headers

If your parser names columns differently, write a YAML file and pass it with
`--columns cols.yaml`. Your names are tried before the defaults:

```yaml
logfile:
  redo_op: RedoOperation
  file_ref: TargetMftRef
  name: FileName
```

Logical column names for each artifact are listed in `wipertrace/config.py`.

## Adding or tuning a signature

Rules live in `wipertrace/signatures/default.yaml`. A new tool usually needs no
Python — combine the four matcher kinds:

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

Check it with `wipertrace rules`, which validates the whole file and lists
every rule. Parameters for each kind are documented in
`wipertrace/engine/matchers.py`.

## Tests

```powershell
pytest
```

The suite generates synthetic cases for every tool and control, checks
attribution under clean and hard shutdown, and runs ablations (journal lost,
Prefetch only, no artifacts). Synthetic cases verify the software's logic;
measure detection accuracy on real acquisitions.

## Limitations

WiperTrace cannot attribute full-disk offline wipers (e.g. DBAN), can be
defeated by tools that corrupt NTFS metadata files, loses evidence when journals
roll over before acquisition, and attributes at tool level only (overwrite
settings are indistinguishable in metadata). Every report states these.

On real system volumes, some BleachBit 6.x and Eraser journal and $MFT rules
also match routine Windows activity (log rotation, temp-file renames, deleted
ETW logs), so those tools can appear as *"also consistent"* without having
run. Weigh any tool whose execution rule (`*-PF-*`) did not match accordingly.
The synthetic test cases contain no such background activity.
