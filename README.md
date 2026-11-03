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

## Install

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # core + tests
pip install -e ".[image]"          # optional: extract artifacts from raw/E01 images
```

Python 3.10+. Parsing relies on established tools you install separately:
[MFTECmd, PECmd, AmcacheParser](https://ericzimmerman.github.io/) and a
$LogFile parser (defaults assume LogFileParser-style columns).

## Quick start (no evidence needed)

```bash
wipertrace demo sdelete --out demo_case          # synthetic SDelete case
wipertrace analyze demo_case --out demo_report   # -> report.json + report.md
```

Scenarios: `sdelete`, `cipher`, `bleachbit6`, `bleachbit4`, `eraser`, and the
negative controls `explorer_delete`, `rename_churn`. Add `--hard-shutdown` to
simulate a power cut before the final journal records.

## Analysing a real case

### 1. Get the parser CSVs

Either point `collect` at an image (read-only; extracts $MFT, $LogFile, $J,
Prefetch, Amcache, hashes them, and runs the EZ tools you name):

```bash
wipertrace collect evidence.E01 --out case01 \
    --mftecmd C:\Tools\MFTECmd.exe --pecmd C:\Tools\PECmd.exe --amcacheparser C:\Tools\AmcacheParser.exe
```

…or run the parsers yourself and put the outputs in one folder. Files are
recognised by name:

| Artifact | Expected file name pattern |
|---|---|
| $UsnJrnl:$J | `*$J_Output*.csv` (MFTECmd) or `*usnjrnl*.csv` |
| $MFT | `*$MFT_Output*.csv` (MFTECmd) or `mft.csv` |
| $LogFile | `*logfile*.csv` |
| Prefetch | `*PECmd_Output.csv` |
| Amcache | `*FileEntries*.csv` (AmcacheParser) or `amcache.csv` |

### 2. Analyse

```bash
wipertrace analyze case01 --out case01_report
```

Useful options:

| Option | Purpose |
|---|---|
| `--usnjrnl F` / `--mft F` / … | Give a CSV explicitly instead of discovery |
| `--drop logfile` | Ignore an artifact class (ablation / robustness tests) |
| `--since "2026-10-14 08:00:00"` `--until …` | Restrict to a UTC time window |
| `--columns cols.yaml` | Map non-default CSV header names (see below) |
| `--signatures my.yaml` | Use another signature database |
| `--format json` | Only write the JSON report |

### 3. Read the report

* **Conclusion** — `attributed`, `ambiguous` (names the tied tools),
  `no_wiper_indicated`, or `insufficient_artifacts`.
* **Support** — share of the tool's *evaluable* signature that matched.
* **Coverage** — share of the tool's signature that could be evaluated with the
  artifacts present. Low coverage = the verdict rests on few artifacts.
* **Confidence** — support × √coverage.
* **Rule results** — every rule as MATCH / – / n/a, with evidence rows
  (`file.csv:row`) you can open in Timeline Explorer.
* **Limitations** and **input manifest** (SHA-256 of every input).

## Mapping different CSV headers

If your parser names columns differently, write a YAML file:

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

```bash
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
