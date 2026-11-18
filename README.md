# WiperTrace

WiperTrace answers a second-order forensic question: **not "what was deleted?"
but "which wiping tool did the deleting, and when?"** It reads the NTFS journals
($UsnJrnl, $LogFile), $MFT residue and Windows execution artifacts (Prefetch,
Amcache), scores the evidence against a database of per-tool signatures, and
reports a ranked, confidence-scored attribution with the CSV row behind every
conclusion.

**Documentation:** [User Manual](docs/USER_MANUAL.md) (installation, step-by-step
case analysis, command reference, troubleshooting) ·
[Architecture](docs/ARCHITECTURE.md) (design and internals)

## Why it exists

Secure-deletion tools leave no recoverable content, so the usual question,
"what was deleted?", often has no answer. But each tool destroys files in its
own way: SDelete renames a file through a fixed sequence of names before
deleting it, cipher /w fills free space with a temporary folder, BleachBit and
Eraser have their own rename and overwrite patterns. NTFS records these
operations in its journals, and Windows records that the program ran. Together
they identify the tool and the time of the wipe, even when the wiped data
itself is gone.

WiperTrace automates that reasoning and makes it reviewable: every conclusion
links to the exact parser-CSV rows that support it, and every report states
how much of the evidence was available and what the method cannot show.

## Supported tools

Signature DB 0.2.0:

| Tool | Version |
|---|---|
| Sysinternals SDelete | 2.05 |
| cipher.exe /w | built into Windows |
| BleachBit | 6.x and 4.x |
| Eraser | 6.2 |
| CCleaner (secure file deletion) | |
| Jetico BCWipe | 7.60 |

New tools can be added as YAML rules without writing Python
([User Manual §10.3](docs/USER_MANUAL.md#103-adding-or-tuning-a-signature)).

## How it works

| Evidence | Lives on | Proves |
|---|---|---|
| $UsnJrnl:$J, $MFT, $LogFile | The volume the wiped files were on | *How* the files were destroyed (rename patterns, overwrites) |
| Prefetch, Amcache | The Windows (system) volume | *Which program ran*, and when |

1. **`collect`** reads a raw or E01 disk image read-only, extracts the
   artifacts with their SHA-256 hashes, and parses them with Eric Zimmerman's
   MFTECmd, PECmd and AmcacheParser.
2. **`analyze`** evaluates each tool's signature rules against the parsed
   CSVs: journal patterns (`-USN-`, `-LOG-`), $MFT residue (`-MFT-`),
   execution evidence (`-PF-`) and their correlation in time (`-COR-`).
3. Each tool gets a **support** (share of its evaluable rules that matched),
   **coverage** (share of its rules the available artifacts allow evaluating)
   and **confidence** (support × √coverage). The report concludes
   `attributed`, `ambiguous`, `no_wiper_indicated` or `insufficient_artifacts`.

## At a glance

```powershell
python -m wipertrace demo sdelete --out demo_case          # synthetic case, no evidence needed
python -m wipertrace analyze demo_case --out demo_report
```

```
ATTRIBUTED: Attributed to Sysinternals SDelete: support 1.00, coverage 1.00, confidence 1.00 (high).
```

On real evidence:

```powershell
python -m wipertrace collect image.E01 --out case --mftecmd MFTECmd.exe --pecmd PECmd.exe --amcacheparser AmcacheParser.exe
python -m wipertrace analyze case --out report
```

Requires Python 3.10+, the .NET 10 runtime, and the Eric Zimmerman tools.
Windows is the recommended platform. See the
[User Manual](docs/USER_MANUAL.md) for the full setup and workflow.

## Validation

The test suite (`python -m pytest`) generates synthetic cases for every tool
and control, checks attribution under clean and hard shutdown, and runs
ablations (journal lost, Prefetch only, no artifacts). Synthetic cases verify
the software's logic; detection accuracy is measured on real acquisitions.

`scripts/` turns the wipertrace-vmware harness runs (72 runs: 7 tools and 2
controls, clean and hard shutdown) into case folders and scores the program on
all of them:

```powershell
python -m pip install dissect.util                      # Prefetch decompression
python scripts\harness_to_case.py "raw data" cases --all
python scripts\evaluate_cases.py cases                  # all artifacts
python scripts\evaluate_cases.py cases --drop usnjrnl   # journal lost
```

With signature DB 0.2.0 all 72 runs are classified correctly with no second
tool listed; with $UsnJrnl dropped, 66/72 (the six BleachBit 4 runs become
*ambiguous* with BleachBit 6). `harness_to_case.py` approximates
LogFileParser's $LogFile output, so treat `*-LOG-*` results from it as
indicative.

## Limitations

WiperTrace cannot attribute full-disk offline wipers (e.g. DBAN), can be
defeated by tools that corrupt NTFS metadata files, loses evidence when journals
roll over before acquisition, and attributes at tool level only (overwrite
settings are indistinguishable in metadata). Every report states these.

On real system volumes, some BleachBit 6.x and Eraser journal and $MFT rules
also match routine Windows activity (log rotation, temp-file renames, deleted
ETW logs), so those tools can appear as *"also consistent"* without having
run. Weigh any tool whose execution rule (`*-PF-*`) did not match accordingly.

CCleaner runs for ordinary cleaning as well as secure deletion. If secure
deletion was off, its journals show an ordinary delete and only `CCL-PF-001`
matches: that shows CCleaner ran, not that it wiped anything.
The synthetic test cases contain no such background activity.
