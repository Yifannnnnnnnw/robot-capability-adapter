# AutoAdapter 2.0 — interactive project page

An English, dependency-free GitHub Pages site with two clearly separated modes:

- **Recorded execution:** an authentic Panda pick-and-place video, with 12
  synchronised driver calls, their requests, public ReCAP plan summaries and
  driver feedback. This works on GitHub Pages without a backend.
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
