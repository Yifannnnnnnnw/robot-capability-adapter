# AutoAdapter 2.0 — interactive project page

An English, dependency-free GitHub Pages site with an execution explorer and
a three-robot film library:

- **Recorded execution:** an authentic Panda pick-and-place video, with 12
  synchronised driver calls, their requests, public ReCAP plan summaries and
  driver feedback. This works on GitHub Pages without a backend.
- **Robot film library:** nine selected recordings across Franka Panda, LEAP
  Hand and Skydio X2, with task selection, still previews and individual physical
  outcomes. Switching robots pauses the previous video.
- **Live local simulation:** connect the page to `local_demo.py`, then run the
  same task with the real model, ReCAP, MCP, generated driver and MuJoCo. This
  requires the existing research checkout and its local generated artifacts.
  It does not connect to physical hardware.

## Start a live demo

From the existing AutoAdapter project checkout:

```sh
AA1/.venv/bin/python website/local_demo.py
```

Open `http://127.0.0.1:8766/`, expand **Connect a local MuJoCo simulation**, select
**Connect**, then **Run live task**. Starting the bridge alone makes no model
calls. A live run uses the configured model account and the saved task's budget.
The **Stop** button terminates that task; Ctrl+C stops the bridge and its worker.

The GitHub page can also connect to this loopback bridge if the browser permits
local-network access. If the browser blocks that connection, use the local page
above. No tunnel, public backend, or credentials in the website are required.
The bridge accepts the project's GitHub Pages origin and its own local origin.
Only its connection token can start or stop a task; API keys stay server-side.

### Required local inputs

- The existing `AA1/.venv` with MuJoCo, MCP, Pillow and model-client dependencies.
- The saved request at
  `expriment/chapter5_cross_robot/data/runs/franka_single_20260915_01/tasks_astra_budget48/requests/mw_pick_place_central.json`.
- The driver, generated MCP server and scene referenced by that request.
- The project's existing model access, loaded by the existing client. No key
  needs to be placed in this directory or entered in the page.

These run artifacts are local research outputs. A fresh public clone alone does
not contain them; the bridge reports missing inputs instead of substituting a
mock. The hosted recorded demo remains usable independently.

`local_demo.py` reuses the request and physical scorer, changing only the output
location and adding presentation observers in fresh worker processes. It leaves
AA1, the generated driver, original MCP server, task criteria and saved evidence
unchanged. All new output goes into the OS temporary directory under
`autoadapter-web-demo-*`. This is a diagnostic demonstration, not a formal trial.

## Content and evidence

Research framing follows the chapters included by `thesis/Main.tex`. No
aggregate success rate is presented. The selected Panda driver's original
validation remains **4/5**, including the failed contact-pressing check. Its
separate downstream task success does not overwrite that result.

| Website asset | Existing local source |
|---|---|
| `assets/panda-pick-place.mp4` | `.../tasks_astra_budget48/mw_pick_place_central/video.mp4` |
| `assets/panda-replay.json` | Whitelisted fields from that run's `task_report.json`, `trace.jsonl`, `runtime_report.json`, and `task_evaluation.json` |
| `assets/panda-tasks.png` | `thesis/assets/chapter5_panda_tasks.png` |
| `assets/leap-tasks.png` | `thesis/assets/chapter5_leap_tasks.png` |
| `assets/skydio-tasks.png` | `thesis/assets/chapter5_skydio_tasks.png` |

The recorded video is unchanged: 480 × 368, 628 frames, 30 fps. Call boundaries
are reconstructed from the actual recording rule: one frame every 16 physics
steps, two initial frames and one post-call observation frame. That yields 628
frames, matching the video exactly. Model waiting time is omitted. The source
video has no embedded per-frame simulation timestamps; the complete alignment
method and limitations are recorded in `panda-replay.json`.

Only public action summaries, requests and selected observations are published.
No raw model-message history, system prompts, absolute local paths, endpoints
or model credentials are included. Driver-reported failures in calls 2 and 6
remain visible despite the independently successful final task.

## Robot film library

The videos are unchanged copies of existing task recordings; thumbnails are
single frames extracted at 40% of playback duration. No new robot run was
performed for this gallery. All sources below are relative to
`expriment/chapter5_cross_robot/data/runs/`; each source directory also contains
the `task_report.json` used for the physical verdict. Durations are encoded
playback time, not wall-clock or simulation time.

| Published clip | Source video | Physical outcome |
|---|---|---|
| `assets/panda-pick-place.mp4` | `franka_single_20260915_01/tasks_astra_budget48/mw_pick_place_central/video.mp4` | Passed; Astra task run |
| `assets/robots/panda-drawer.mp4` | `franka_single_20260915_01/tasks_astra_budget48/mw_drawer_open_central/video.mp4` | Passed; Astra task run |
| `assets/robots/panda-dial.mp4` | `franka_single_20260915_01/tasks_astra_budget48/mw_dial_turn_central/video.mp4` | Passed; Astra task run |
| `assets/robots/leap-reach.mp4` | `leap_single_20260915_01/tasks_astra_direct_retry_20260915/gym_hand_reach_all_fingertips_central/video.mp4` | Passed; Direct retry |
| `assets/robots/leap-block.mp4` | `leap_single_20260915_01/tasks_astra/gym_hand_manipulate_block_full_pose_central/video.mp4` | Not passed; First attempt |
| `assets/robots/leap-pose.mp4` | `leap_single_20260915_01/tasks_astra/robel_dclaw_pose_fixed_central/video.mp4` | Not passed; First attempt |
| `assets/robots/skydio-transit.mp4` | `skydio_single_20260915_01/tasks_astra_diagnostic/X2-T04_central/video.mp4` | Passed; First diagnostic attempt |
| `assets/robots/skydio-waypoints.mp4` | `skydio_single_20260915_01/tasks_astra_diagnostic/X2-T05_central/video.mp4` | Passed; First diagnostic attempt |
| `assets/robots/skydio-orbit.mp4` | `skydio_single_20260915_01/tasks_astra_orbit_clarified_20260915/X2-T07_central/video.mp4` | Not passed; Clarified-task retry |

LEAP uses the unchanged generated driver after three missing MCP forwarding
wrappers were completed manually; capability validation remains 4/6. Its reach
clip is a direct retry, while cube manipulation and joint-pose matching are
first attempts. Skydio uses an assisted export after automatic generation
reached its turn limit; separate native capability validation remains 4/5.
The orbit is a clarified-task retry and still fails the inward-facing bearing
criterion. These selections are not a new experiment or aggregate success rate.

The website uses one continuous, soft gradient backdrop with neutral translucent
surfaces. Stage-colored strips, borders and separate color blocks have been removed.

Verified on 16 September 2026: all nine recordings decode, local media links
resolve, LEAP and Skydio play in the browser, task changes update the result
caption, and keyboard robot switching works. Desktop and 390 px layouts have
no horizontal overflow. The original Panda call replay still exposes its
recorded failed request. No live model run was needed for this presentation update.

## Publishing

GitHub Pages serves the root of the `gh-pages` branch in our parent repository,
`Yifannnnnnnnw/robot-capability-adapter`. The branch contains this directory's
contents; no framework, package installation, Actions workflow or build is
needed. Keep `.nojekyll` and the small, explicitly selected demo MP4.

To update, copy this directory's files into a checkout of that repository's
`gh-pages` branch, inspect the diff, commit and push that branch. Exclude
`__pycache__` and local outputs. Do not force-push or push local `main` history
just to deploy the page. Never push to AA1's original/upstream repository.

GitHub's [publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
explains branch-based hosting.

### Printed QR code redirect

The printed poster QR code points to
`https://yifannnnnnnnw.github.io/auto-adapter/`. Keep that address available:
the separate `Yifannnnnnnnw/auto-adapter` repository publishes only the contents
of `qr-redirect/` from its `main` branch root. It forwards visitors to
`https://yifannnnnnnnw.github.io/robot-capability-adapter/`, with an immediate
browser redirect and a visible link as a fallback. The printed QR code does
not need replacement. This repository is only a redirect, not an AA1 upstream.

## Focused verification

Check local asset links, English copy, JavaScript/Python syntax, desktop and
mobile layouts, video seeking, case tabs and keyboard navigation. Video serving
supports byte ranges, so selecting a call also seeks to the matching frame.
For the live mode, run one actual task through the browser and verify live plan,
call, frame and final-report updates. Reconnecting should recover that run.

### Verified on 15 September 2026

A fresh local browser-triggered run completed through the real GPT-6 Astra →
ReCAP → MCP → generated Panda driver → MuJoCo path: 10 tool calls, 17 public
plan events, 603 recorded frames, 18.924 seconds of simulated task motion and
94.81 seconds wall time. Execution and independent physical checks both passed.
The browser received live frames and recovered the completed verdict after a
reload and reconnection. This run is a diagnostic demo, excluded from thesis
experiment results.

Recorded call seeking, both recorded driver failures, keyboard case switching,
English copy, local links, syntax and a 390-pixel mobile viewport were checked.
A video range request returned 206 with exactly the requested 100 bytes after
fixing local seeking; the previous response was 200 with the whole video.

The published page and recorded-call seeking were also checked on GitHub Pages.
This browser timed out when connecting from the hosted origin to loopback;
the page therefore includes a direct **Open local demo** fallback. The local
page is the verified route for live execution in this browser.
