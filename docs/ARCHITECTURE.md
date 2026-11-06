# WiperTrace — Architecture

WiperTrace attributes NTFS file-wiping activity to the tool that performed it.
It reads file-system journals, $MFT residue and Windows execution artifacts,
evaluates them against a database of per-tool signatures, and produces a ranked,
confidence-scored attribution with an auditable evidence trail.

This document describes how the software is structured, the data contracts
between its components, and how signatures and scoring work.

---

## 1. Design principles

| Principle | What it means in code |
|---|---|
| **Read-only** | Nothing writes to the evidence. Acquisition copies artifacts out of an image; every input file is SHA-256 hashed into a manifest before analysis. |
| **Parse with established tools, interpret in WiperTrace** | WiperTrace does not re-implement $MFT/$J/Prefetch parsing. MFTECmd, PECmd, AmcacheParser and a $LogFile parser produce CSV; WiperTrace normalises that CSV. |
| **Signatures are data, not code** | Per-tool behaviour lives in `signatures/default.yaml`. Supporting a new tool means adding YAML rules, not Python. |
| **Every rule declares the artifacts it needs** | If an artifact is missing, empty or rolled over, the rules that need it are marked `not_evaluable` rather than `not_matched`, so the result degrades gracefully. |
| **Every verdict carries its evidence** | Each matched rule stores the exact rows (file + row number) that satisfied it, so every conclusion can be traced back to the parser output. |
| **Explicit scope** | Known limitations (offline wipers, metadata corruption, journal roll-over, indistinguishable overwrite modes, provisional rules) are printed in every report. |

---

## 2. Pipeline

```
  ┌─────────────────────┐   E01 / raw image                 (optional: parser CSVs
  │ 1. ACQUISITION      │──► pytsk3/pyewf extract            can be supplied directly)
  │  acquisition/       │    $MFT, $LogFile, $J, *.pf,
  │                     │    Amcache.hve  ──► run EZ tools
  └─────────┬───────────┘
            │  case directory of parser CSVs  + manifest (SHA-256)
            ▼
  ┌─────────────────────┐
  │ 2. EXTRACTORS       │  one per artifact class; tolerant column mapping
  │  extractors/        │  CSV row ──► Event(source, ops, ts, file_ref, name, attrs, origin)
  └─────────┬───────────┘
            │  EventStore  { usnjrnl: [...], mft: [...], logfile: [...], prefetch: [...], amcache: [...] }
            │  + ArtifactStatus  (present / empty / missing / error per class)
            ▼
  ┌─────────────────────┐
  │ 3. RULE ENGINE      │  load + validate signatures YAML
  │  engine/            │  for each rule: evaluable? ──► matcher.evaluate() ──► RuleResult
  │                     │  correlate rules run last (cross-artifact time windows)
  └─────────┬───────────┘
            │  [RuleResult(rule, status, strength, evidence[])]
            ▼
  ┌─────────────────────┐
  │ 4. SCORING/REPORT   │  per-tool support, coverage, confidence
  │  scoring.py         │  ranked verdicts + conclusion (attributed / ambiguous /
  │  report/            │  no wiper indicated / insufficient artifacts)
  └─────────────────────┘  ──► wipertrace_report.json  +  wipertrace_report.md
```

---

## 3. Module map

```
wipertrace/
├── pyproject.toml
├── README.md                      user manual (install, collect, analyze, demo)
├── docs/ARCHITECTURE.md           this file
├── wipertrace/
│   ├── cli.py                     `wipertrace collect | analyze | rules | demo`
│   ├── models.py                  Event, Origin, Rule, RuleResult, ToolVerdict, AnalysisResult
│   ├── util.py                    op-name canonicalisation, timestamp / int parsing, hashing
│   ├── config.py                  column-name candidates, discovery patterns, scoring thresholds
│   ├── case.py                    find input CSVs, hash them, build the manifest
│   ├── pipeline.py                case → extract → evaluate → score → AnalysisResult
│   ├── acquisition/
│   │   ├── image.py               open raw/E01, find NTFS volumes, copy out system files (pytsk3)
│   │   └── eztools.py             run MFTECmd / PECmd / AmcacheParser on extracted files
│   ├── extractors/
│   │   ├── base.py                CSV reader, column resolution, Extractor ABC
│   │   ├── usnjrnl.py             MFTECmd $J CSV
│   │   ├── mft.py                 MFTECmd $MFT CSV (+ derived: name_len, all_ts_1601 …)
│   │   ├── logfile.py             $LogFile CSV (LogFileParser-style columns by default)
│   │   ├── prefetch.py            PECmd CSV (one event per recorded run time)
│   │   └── amcache.py             AmcacheParser *FileEntries CSV
│   ├── engine/
│   │   ├── signatures.py          YAML loading + validation (ids, tools, kinds, requires)
│   │   ├── context.py             event store wrapper, cached groupings (rename chains, per-file)
│   │   ├── matchers.py            rename_chain, per_file_count, match, correlate
│   │   └── evaluator.py           availability check, evaluation order, RuleResult
│   ├── scoring.py                 support / coverage / confidence, ranking, conclusion
│   ├── report/
│   │   ├── json_report.py
│   │   └── markdown_report.py
│   ├── testing/synth.py           synthetic case generator (tests and `wipertrace demo`)
│   └── signatures/default.yaml    bundled signature database
└── tests/
```

---

## 4. Data contracts

### 4.1 Event (common schema produced by every extractor)

| Field | Type | Notes |
|---|---|---|
| `source` | str | `usnjrnl`, `mft`, `logfile`, `prefetch`, `amcache` |
| `ops` | frozenset[str] | Canonical UPPER_SNAKE operations: `RENAME_NEW_NAME`, `FILE_DELETE`, `CREATE_ATTRIBUTE`, `EXECUTION`, `MFT_RECORD` … |
| `timestamp` | datetime (UTC) or None | $LogFile records usually have none |
| `file_ref` / `seq` / `parent_ref` | int or None | MFT entry, sequence, parent entry |
| `name` | str or None | File name / executable name |
| `attrs` | dict | Source-specific extras (size, in_use, parent_path, redo/undo op, run_count …) |
| `origin` | Origin(artifact, path, row) | Audit pointer back to the parser CSV |
| `order` | int | USN / LSN / row order inside the source (sequence matters for rename chains) |

Operation names are canonicalised (`SetBitsInNonresidentBitMap` →
`SET_BITS_IN_NONRESIDENT_BIT_MAP`), so rules can be written in either spelling.

### 4.2 Rule (signature database entry)

```yaml
- id: SDEL-USN-001
  tool: sdelete
  description: 26 single-letter renames per file (AAAA.AAA … ZZZZ.ZZZ)
  requires: [usnjrnl]            # each entry may be "a|b" = any of
  weight: 4.0
  calibrated: true               # false = provisional threshold
  kind: rename_chain
  params: { min_renames: 20, letter_progression: 20, ... }
```

Matcher kinds (all generic; new tools reuse them):

| kind | Checks | Typical use |
|---|---|---|
| `rename_chain` | Per file: number of renames, name regex, alphabetic progression, same length as original, positional length pattern, time span, exclusions | SDelete letter renames, BleachBit 6 same-length rename, BleachBit 4 long→short, Eraser ×7 |
| `per_file_count` | Per file: counts of given operations within ranges (`A|B` sums alternatives) | $LogFile Create/DeleteAttribute pairs, Eraser BASIC_INFO_CHANGE ×16, cipher bitmap operations |
| `match` | Events whose fields satisfy predicates (`regex`, `iregex`, `eq`, `ne`, `in`, `range`, `prefix`, `contains`, `exists`), optionally counted per file | Prefetch/Amcache executable names, EFSTMPWP folder, $MFT residue |
| `correlate` | Referenced rules matched **and** their evidence lies within a time window | "rename burst within 15 min of an SDELETE64.EXE run" |

Validation happens at load time: unknown kinds, tools, artifacts or parameters,
duplicate ids, non-positive weights and invalid `correlate` references are all
reported together. A `correlate` rule inherits the `requires` of the rules it
references.

### 4.3 RuleResult → ToolVerdict

Each rule ends in one of three states: `matched`, `not_matched`, or
`not_evaluable` (a required artifact is missing, empty or failed to parse).

For each tool *T*:

* **support** = Σ(weight × strength of matched rules) / Σ(weight of evaluable rules)
* **coverage** = Σ(weight of evaluable rules) / Σ(weight of all rules)
* **confidence** = support × √coverage

Support measures how much of the tool's *testable* signature is present;
coverage measures how much of the signature could be tested at all; confidence
discounts verdicts that rest on few artifacts.

Conclusion logic (thresholds in `config.SCORING`):

| Condition | Conclusion |
|---|---|
| No artifact present | `insufficient_artifacts` |
| Top support < `attribute_threshold` | `no_wiper_indicated` |
| Top two within `ambiguity_margin` | `ambiguous` (all tied tools named) |
| Otherwise | `attributed`; other tools ≥ `detect_threshold` listed as also consistent |

Several tools can be reported as "also consistent" because more than one wiper
may have run on the same volume.

---

## 5. Signature rationale

The bundled database encodes the following observed behaviours (Windows 10/11
NTFS, one build per tool).

| Tool | Behaviour | Rule(s) |
|---|---|---|
| SDelete 2.05 | 26 single-letter renames per file, then a file-ID name | `SDEL-USN-001` |
| | Letter names and ~27 Create/DeleteAttribute pairs per file in $LogFile | `SDEL-LOG-001`, `SDEL-LOG-002` |
| | 24-char file-ID name left in `$Extend\$Deleted` | `SDEL-MFT-001` |
| | Final FILE_DELETE absent after a hard shutdown | no SDelete rule requires `FILE_DELETE` |
| cipher /w | EFSTMPWP fill directory; hundreds of bitmap operations per fill file | `CIPH-USN-001`, `CIPH-MFT-001`, `CIPH-LOG-001` |
| BleachBit 6.x | One same-length random rename per file; size truncated to 0 | `BB6-USN-001`, `BB6-MFT-001` |
| BleachBit 4.x | ~226-char rename, then 1-char rename; record overflow in $LogFile | `BB4-USN-001`, `BB4-MFT-001`, `BB4-LOG-001` |
| Eraser 6.2 | 7 random renames, ~16 BASIC_INFO_CHANGE, timestamps set to 1601-01-01 (the $FILE_NAME ones too; NTFS rewrites $SI LastRecordChange) | `ERA-USN-001`, `ERA-USN-002`, `ERA-MFT-001`, `ERA-LOG-001` |
| CCleaner (secure deletion, 1 pass) | One rename per file to a same-length all-Z name (`ZZZZZZ.ZZZ`), overwrite, delete; Z name left in $MFT, size kept (rounded up to 512 bytes) | `CCL-USN-001`, `CCL-MFT-001` |
| BCWipe 7.60 | Rename to a random lower-case name (8.3 style for 8.3 names, else same length); data **and** an alternate data stream wiped (NamedDataExtend), truncated, deleted; $SI times set to 1601 (+4096..32768 ticks), $FN kept; `~BCWipe.tmp\LOGFILEWIPER` floods $LogFile | `BCW-USN-001..003`, `BCW-MFT-001`, `BCW-MFT-002`, `BCW-LOG-001` |
| All | Executable name in Prefetch / Amcache | `*-PF-001` (`requires: [prefetch|amcache]`) |
| All | Journal activity within 15 min of the tool's execution | `*-COR-001` |

Design decisions that follow from these behaviours:

* **Tool-level attribution only.** Overwrite settings (e.g. SDelete `-p1` vs
  `-p3`, Eraser Gutmann vs pseudorandom) leave identical metadata traces.
* **Amcache is never required.** It is populated by a scheduled inventory task
  and often lacks entries for short-lived executions, so it is only ever an
  alternative to Prefetch.
* **Look-alike names are separated by count and character set.** CCleaner's
  `ZZZZZZ.ZZZ` is also SDelete's 26th name, but CCleaner renames once
  (`max_renames: 1`). BleachBit 6's same-length names mix digits and
  punctuation, so `BB6-USN-001` excludes names of one repeated letter
  (CCleaner) and letters-only names (BCWipe).
* **BleachBit versions share an executable name.** With Prefetch alone the two
  versions are reported as `ambiguous`; journal rules separate them.
* **Provisional rules.** Rules whose thresholds come from qualitative
  observations are marked `calibrated: false`; matched provisional rules are
  listed in the report's limitations.

---

## 6. Extension points

| To… | Change |
|---|---|
| Support a new tool | Add a `tools:` entry and rules to a signature YAML; no code change unless a new behaviour cannot be expressed by the four matcher kinds. |
| Add a matcher kind | Subclass `Matcher` in `engine/matchers.py`, declare `kind` and `allowed` params, register it in `KINDS`. |
| Read a different parser's CSV | Pass `--columns` with a YAML mapping of logical → actual column names, or add candidates in `config.COLUMNS`. |
| Add an artifact class | Add an `Extractor` subclass, register it in `extractors.REGISTRY`, add its name to `ARTIFACTS` and discovery patterns. |
| Replace scoring | `scoring.score()` consumes only `RuleResult`s; a learned model can replace it without touching extractors or the engine. |
| Add a report format | Add a writer in `report/` and register it in `report.WRITERS`. |

---

## 7. Current limitations

* Events are held in memory. Very large `$J` exports (millions of rows) would
  benefit from an SQLite- or dataframe-backed event store; the `EvalContext`
  interface isolates this change.
* `$LogFile` column defaults match LogFileParser output (`lf_RedoOperation`,
  `lf_MFTReference`, …); other parsers need a column mapping.
* BleachBit 4.x and 6.x cannot be separated from Prefetch executable names alone.
* CCleaner is only attributable when secure deletion was on; its execution
  alone (`CCL-PF-001`) does not show a wipe.
* `BCW-LOG-001` (UpdateFileNameRoot flood) was measured with an approximate
  $LogFile parser (`scripts/harness_to_case.py`) and is marked provisional.
* Full-disk offline wipers (e.g. DBAN) leave no live-OS artifacts and are out of
  scope; tools that corrupt NTFS metadata files can defeat journal-based rules.
