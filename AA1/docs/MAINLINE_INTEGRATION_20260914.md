# Mainline integration check — 2026-09-14

The default local pipeline is now:

```text
Study → Design (generate or load) → Generate ↔ Validate → Export
                                                           └─ enable_demo → ReCAP → MCP
```

Standard and from-scratch generation remain supported. The launcher calls the
public orchestrator run; `--stop-after` deliberately truncates the chain.
DEMO uses the configured task/scene and discovers the export's actual MCP
tools. It does not require fixed capability method names.

## Local checks

- 22 configuration IDs across 17 robot families: all real MuJoCo scenes and
  initial states loaded (28 focused configuration checks).
- Nine scene sets preserved unchanged under `assets/mjcf/demo_scenes/` before
  deleting the old fixed capability scenes, suites, scorers and references.
- Orchestrator/launcher checks: 60 passed before the input-reuse fix; the
  11-test task-grounded suite then passed with its new two-route regression.
  Root review reran 28 entry, input-reuse and native quadruped checks.
- The input-reuse regression first reproduced deletion of supplied design and
  case files inside the workspace. Both routes now preserve those inputs and
  clear stale generated output.
- MCP integration: 46 ReCAP/client/task/stage checks passed. The official
  vendored controller remains the implementation of tree traversal.
- EXPORT runtime: four real MuJoCo checks passed, including video creation;
  three SDK schema checks passed. These use named test fixtures where needed.

## Fresh public-run Piper diagnostic

Local output: `AA1/artifacts/mainline_piper_20260914_l7vfjlzm/`.
The diagnostic used the actual Piper MJCF, current public task library and
Holistic Claude Sonnet 4.6. It did not reuse a driver or design.

| Phase | Observed result |
| --- | --- |
| STUDY | Completed |
| DESIGN | Completed: 20 model rounds, about 247 seconds; 5 capabilities, 7 criteria, 5 cases, 2 scenes |
| GENERATE | Completed |
| VALIDATE | 3/5 cases passed, with five real simulation videos |
| REPAIR | Reached the existing 22-round limit before submitting a successful repair |
| EXPORT | Correctly blocked |

The failed cases were wrist roll (0.066877 rad error versus 0.05 rad) and
contact push (0.072875 m terminal end-effector error versus 0.03 m). Contact
force itself met its criterion. Full details, actual messages, samples and
video paths are in `piper/summary.json`, `piper/narrative.md`,
`piper/validate_report.json`, `piper/design/` and `piper/traces/`.

The reach criterion adopted 2 cm. DESIGN separately proposed 5 cm for waypoint
tracking and 3 cm for contact-push end-effector positioning; this run does not
establish uniformly tightened 2 cm capability criteria. The push measurements
also do not constitute an object-at-goal predicate.

## Prepared scenes supplied to generation and repair

Implemented in `4e4e98b9`. Both generation routes now export
`design/probe_scenes.json` from the current DESIGN inputs. It contains scene
paths, capability associations and initial states; case requests, measurement
bindings and execution limits are excluded. All four generation/repair prompts
provide this file and a `local_exec` example. Probes and validation share
`build_scene_driver()` and `reset_scene_driver()`, staging the candidate with
the selected prepared MJCF in a separate directory.

Local output: `AA1/artifacts/scene_handoff_piper_xdpqsctk/`. This check reused
the fresh run's unchanged candidate and DESIGN; it made no model calls.
Two actual `local_exec` probes loaded the prepared `push_block` scene with
identical initial states. The source candidate and workspace MJCF were
unchanged. The five validation cases were rerun with real MuJoCo videos:
all seven measurement results exactly matched the original report, retaining
the 3/5 verdict. All five videos decoded successfully. The focused root checks
passed 33 tests; a separate scratch runtime check confirmed that the scene
helpers remain importable without an inherited `PYTHONPATH`.

The handoff defect is fixed and locally checked. A new model-driven repair
has not yet been run, so this does not establish that the two failed
capabilities now pass. Readable details are in `HANDOFF_REVIEW.md`; tool
outputs, the public scene file and the validation comparison are preserved
alongside it. These local diagnostic outputs are not committed to Git.

## MCP DEMO diagnostic on main

Local output: `AA1/artifacts/mcp_demo_main_20260914_z01n9n1w/`.
This separate check explicitly reused the previously generated and validated
Piper driver and export from `/private/tmp/aa1-default-design-export/diagnostics/piper`.
Their copied contents were unchanged. It did not export the new failed driver.

The current main orchestrator dispatched the official ReCAP controller through
the real SDK client. Four model calls selected three successful MCP calls:
`traverse_waypoints` once, then `set_gripper_aperture` at 0.007 m and 0.028 m.
The session used `assets/mjcf/demo_scenes/piper/fixed_scene.xml` and its
configured initial state. Simulation time advanced from 0.5 to 7.9 seconds;
the video decoded to 236 frames and the runtime reported no error.

Reports and video are under `piper/demos/recap-l9twgk48/task/`:
`task_report.json`, `mcp_tools.json`, `model_messages.jsonl`,
`runtime_report.json`, `recap/tree.json`, and `video.mp4`.

This verifies the merged MCP DEMO execution path. `physical_task_success`
remains null because independent DEMO task predicates are not implemented.
The fresh generation run has not passed the complete chain. These are local
diagnostics, not a formal experiment or a new experimental denominator.
