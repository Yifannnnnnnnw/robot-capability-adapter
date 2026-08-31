# Experiment archive

This directory contains read-only historical run evidence moved from the
former active experiment roots:

- `runs/experiment1/` — former `experiment/experiment1/runs/` (B1)
- `runs/b2_recap/` — former `experiment/b2_recap/runs/` (B2)
- `pre_thesis_realign_2026-08-31/` — the four complete experiment workspaces,
  old runner-specific shared tests, and launch runbook retired before new
  thesis-aligned experiments are designed.

Raw evidence under `runs/` is not tracked in Git and is not rewritten during
the directory migration.  The one concurrent B1 run that recreated an old
directory after the initial move used an already occupied run-directory name.
Its newer files were retained at the canonical archived path; the two
non-overwritable pre-migration versions of `scheduler_record.json` and
`cell_record.json` remain under
`runs/experiment1/_migration_fragments/`.  Their run identifiers and bytes are
unchanged.

Migration reconciliation (regular files only) was:

| archive | pre-migration baseline | concurrent late delta | final archive total |
| --- | ---: | ---: | ---: |
| B1 | 17,123 files / 10,287,396,689 bytes | 36 files / 13,826,750 bytes | 17,159 files / 10,301,223,439 bytes |
| B2 | 90 files / 71,537,674 bytes | none | 90 files / 71,537,674 bytes |

The B1 late delta consists of the recreated old-root set (36 files /
13,768,044 bytes) and 58,706 bytes written to an already moved run while an
open-fd log was finishing.  No target was overwritten; the final total is the
baseline plus that late delta.
