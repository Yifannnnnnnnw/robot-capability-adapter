# A4 focused audit — 2026-09-10

This is an offline review of existing diagnostic evidence. No new model calls,
physics runs, driver edits, criteria changes, or historical result changes were made.
The executable implementation reviewed is commit `cbf2dab5`.

## Observed failures

Re-evaluating 18 generated A4 condition traces from Franka, SO-101, and KUKA
reproduced all 18 stored scores. Tracing the existing `_contact_approach` function
identified the first returned verdict without modifying that function:

- 13 cases: no valid precontact dwell/ray entry (`capability_metrics.py:459`).
- 3 cases: no continuous 0.1 s target-contact window (`:465`).
- 1 case: excessive speed within the scored contact window (`:492`).
- 1 case passed: Franka repair 2 nominal (`:496`).

All six latest physically validated A4 cases first fail the precontact gate:

| Candidate | Nominal | Boundary |
|---|---|---|
| SO-101 repair 2 | Within 15 mm and contact-free for only 0.098 s | Only 0.092 s |
| KUKA repair 3 | Within 15 mm and contact-free for only 0.076 s | Only 0.076 s |
| Franka repair 3 | Dwell exists, but every eligible endpoint exceeds the 10 mm lateral gate; minimum 10.910 mm | Same lateral failure |

The dwell requirement is 0.100 s. These are first rejecting gates, not proof that
all subsequent gates would pass after a correction. SO-101 repair 3 was interrupted
by a transport error and has no new physical validation result.

The earlier attribution of these final failures to post-contact overspeed was
incorrect. Franka repair 2 boundary does actually fail that speed gate: the scored
window maximum is 0.028850 m/s versus 0.020 m/s. Its summary reports 0.030206 m/s,
which uses a different window.

## Public contract and scorer discrepancies

1. The public `capability_design.json` declares a 0.1 s **contact** hold, but omits
   the separate 0.1 s **precontact** dwell, its 15 mm neighborhood, and the 10 mm
   lateral entry gate. For example, see the KUKA design at lines 312–364 and
   `autoadapter_bench/capability_metrics.py:433–459`.
2. AA2's `autoadapter/B1_DRIVER_CAPABILITY_BENCHMARK_SPEC.md:199–213` describes the
   ray origin as the measured tool position at the end of the dwell. Both AA1 and
   the AA2 scorer instead subtract the requested precontact position. AA2 prose
   also specifies nearest-surface relative velocity and a first-contact normal
   closing-speed check; these A4 scorer functions use sampled tool-point speed.
   These inconsistencies require a deliberate specification decision, rather
   than assuming every current implementation detail is the intended contract.
3. `describe_contract_measurements` reports best precontact distance but no dwell
   duration, lateral-entry measurement, or first failed gate. Its maximum speed
   after contact includes the first contact-entry sample and the rest of the
   trace. Scoring checks samples after the start of the first continuous 0.1 s
   contact window, through that window's end. The reported maximum therefore
   cannot by itself identify the scoring failure.

## Feasibility evidence

All six existing reference A4 traces (three robots × nominal/boundary), under
`diagnostics/capability_calibration/<robot>/framework_reference/`, still score 1
when evaluated with the current requests and current scorer. They are explicitly
reference controls and do not count as model-generated results. This supports
physical attainability in these fixed scenes; it does not resolve the public
contract inconsistencies.

## Recommended next change

Reconcile the intended A4 semantics with the approved AA2 public specification,
publish every applicable gate in the sole public capability design, and produce
failure feedback from the same gates/windows used by scoring. Preserve thresholds
and existing results while making any semantic correction an explicit new code
batch. Add only focused checks for the observed inconsistencies, then exercise
the real pipeline with the corrected public input. Do not add more repairs to a
completed run or silently reinterpret its original verdicts.

Detailed per-case paths, measurements, exact return lines, current-score
reproduction, and reference checks are in `a4_audit_20260910.json` beside this file.
