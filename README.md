# AutoAdapter 2.0 — interactive project page

An English, dependency-free GitHub Pages site with an execution explorer and
a three-robot film library:

- **Recorded execution:** an HD replay of the recorded Panda task, with 12
  synchronised driver calls, their requests, public ReCAP plan summaries and
  driver feedback. This works on GitHub Pages without a backend.
- **Robot film library:** ten selected recordings across Franka Panda, LEAP
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

The displayed video is a native 1440 × 1080 replay at actual simulation speed:
592 frames at 30 fps, showing 19.674 seconds of recorded motion. It re-executes
the original requests through the saved exported driver and MuJoCo, checking
the resulting trajectory against the original state trace. It makes no new
model calls. Each call's video boundary is its recorded simulator time minus
the initial simulator time (0.3 seconds). The complete alignment method is
recorded in `panda-replay.json`. Initial and terminal poses are included; the
terminal frame adds less than two frames to the encoded duration. Model
waiting time is omitted.

Only public action summaries, requests and selected observations are published.
No raw model-message history, system prompts, absolute local paths, endpoints
or model credentials are included. Driver-reported failures in calls 2 and 6
remain visible despite the independently successful final task.

## Robot film library

The videos are native 1440 × 1080 MuJoCo renders, encoded as H.264 at 30 fps;
thumbnails are single frames extracted at 40% of playback duration. LEAP and
Skydio restore the saved joint and free-body states without advancing physics.
Panda replays the recorded requests through the existing driver, checking the
trajectory against the original trace before rendering. These are presentation
replays, not new planner runs or experiment results. The original research
recordings and verdicts are unchanged. All sources below are relative to
`expriment/chapter5_cross_robot/data/runs/`; each source directory also contains
the `task_report.json` used for the physical verdict. Durations are encoded
playback time at 1× simulation speed. Each video covers its complete recorded
task; model waiting time is omitted. Uniform 30 fps output uses the nearest
recorded physics-step pose, with a brief terminal frame hold of less than two
video frames. This replaces the old every-16-steps cadence, which accelerated
Skydio motion by about 4.8×.

| Published clip | Original recording / trace directory | Physical outcome |
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
| `assets/robots/skydio-orbit-full.mp4` | `skydio_single_20260915_01/tasks_astra_diagnostic/X2-T07_central/video.mp4` | Not passed; First diagnostic attempt |

LEAP uses the unchanged generated driver after three missing MCP forwarding
wrappers were completed manually; capability validation remains 4/6. Its reach
clip is a direct retry, while cube manipulation and joint-pose matching are
first attempts. Skydio uses an assisted export after automatic generation
reached its turn limit; separate native capability validation remains 4/5.
The clarified orbit retry fails the inward-facing bearing criterion. The
extended first orbit attempt lasts 68.93 simulation seconds, exceeds the 60 s
limit and ends slightly short of a full revolution. Both retain their original
failed task verdicts. These selections are not a new experiment or aggregate
success rate.

### Regenerate the HD media

From the original research checkout, with its saved scenes, drivers and traces:

```sh
AA1/.venv/bin/python website/scripts/render_hd_states.py
AA1/.venv/bin/python website/scripts/render_hd_panda.py
```

Both scripts need the existing MuJoCo environment, an offscreen graphics context,
and FFmpeg. They write website media and temporary presentation outputs only.
The 4:3 render preserves the original camera and avoids the old encoder's
480 × 360 to 480 × 368 height expansion. Videos now follow recorded simulation
time; the execution explorer’s call boundaries use that same clock. MP4 metadata
is placed first for browser playback.

The website uses one continuous, soft gradient backdrop with neutral translucent
surfaces. Stage-colored strips, borders and separate color blocks have been removed.

### Real-time playback verification

The original full Panda traces, contacts and call-endpoint states are checked
before rendering. Every LEAP/Skydio rendered pose is checked against the saved
bindings. Output checks cover 1440 × 1080 dimensions, 30 fps, complete decoding,
and real-time duration including the short terminal frame. No live model run
is needed for this presentation update.

Checked on 16 September 2026: all 10 videos decode correctly (7,410 frames).
Browser playback of the 69 s orbit, Panda call seeking, and the 390 px layout
passed. Both standalone QR images and the combined share card decoded to
the exact project and LinkedIn URLs using native barcode detection.

## Publishing

GitHub Pages serves the root of the `gh-pages` branch in our parent repository,
`Yifannnnnnnnw/robot-capability-adapter`. The branch contains this directory's
contents; no framework, package installation, Actions workflow or build is
needed. Keep `.nojekyll` and the explicitly selected demo media.

To update, copy this directory's files into a checkout of that repository's
`gh-pages` branch, inspect the diff, commit and push that branch. Exclude
`__pycache__` and local outputs. Do not force-push or push local `main` history
just to deploy the page. Never push to AA1's original/upstream repository.

GitHub's [publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
explains branch-based hosting.

### Shareable QR code

`assets/project-qr.png` and `assets/project-qr.svg` encode the public gallery:
`https://yifannnnnnnnw.github.io/robot-capability-adapter/#cases`.
`assets/linkedin-qr.png` and `assets/linkedin-qr.svg` encode the user-provided
profile: `https://www.linkedin.com/in/yifan-wang-58203a2a8/`.
The **Share by QR** link opens both codes, clearly labelled **Project demos**
and **LinkedIn**, plus individual downloads. `assets/share-card.png` and
`assets/share-card.svg` combine both codes in one shareable card. The PNG is
suitable for sharing; the SVG stays sharp at print sizes. Public recorded
videos work on a phone without a login or local simulator. The author name
also links to LinkedIn.

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
