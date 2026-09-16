const $ = id => document.getElementById(id);
const video = $('robot-video');
let replay, selectedCall = 0, live = false, bridge, token, polling = false, eventCursor = 0;

function showCall(call, recorded = true) {
  $('plan-summary').textContent = call.planSummary || 'Waiting for the next public plan summary…';
  $('tool-name').textContent = call.name || 'Awaiting a capability call';
  $('tool-request').textContent = call.request ? JSON.stringify(call.request, null, 2) : '—';
  const result = call.driverResult;
  $('feedback-label').textContent = recorded ? '· RECORDED RESPONSE' : '· LIVE RESPONSE';
  $('driver-feedback').className = !result ? 'pending' : result.success === false ? 'warning' : result.success === true ? 'ok' : 'pending';
  $('driver-feedback').textContent = !result ? 'The driver is executing. No result has been received.' : result.success === false ? `Driver reported an unmet request: ${result.reason || 'success = false'}.` : result.success === true ? 'Driver reported success for this request. The task verdict is assessed separately.' : result.reason || 'The tool returned; no driver success flag is available.';
}
function showRecordedCall(index, seek = false) {
  if (!replay || live) return;
  selectedCall = Math.max(0, Math.min(index, replay.calls.length - 1));
  const call = replay.calls[selectedCall];
  $('call-picker').value = String(selectedCall);
  $('call-counter').textContent = `${call.index} / ${replay.calls.length} calls`;
  showCall(call);
  if (seek) { video.pause(); video.currentTime = call.videoStart + .01; }
}
function recordedMode() {
  live = false;
  document.querySelector('.playback-controls').hidden = false;
  document.querySelector('.call-picker-label').hidden = false;
  $('call-picker').hidden = false;
  $('live-frame').hidden = true;
  video.hidden = false;
  $('mode-label').textContent = 'Recorded execution';
  $('playback-note').textContent = '1080p replay at 1× simulation speed, aligned to the original driver calls. Model waiting time is omitted.';
  ['play-run', 'call-picker', 'previous-call', 'next-call', 'playback-speed'].forEach(id => $(id).disabled = !replay);
  if (replay) {
    $('task-verdict').textContent = replay.independentEvaluation.physicalTaskSuccess ? 'Recorded task: passed' : 'Recorded task: not passed';
    $('task-result-description').textContent = 'Block released and supported for 2.502 s (required: 0.2 s). Final goal distance: 11.84 mm (limit: 70 mm). This is one recorded task, not a success rate.';
    showRecordedCall(selectedCall);
  }
  syncPandaView();
}
async function loadReplay() {
  try {
    const response = await fetch('assets/panda-replay.json?v=realtime1');
    if (!response.ok) throw new Error('Replay data could not be loaded.');
    replay = await response.json();
    $('call-picker').replaceChildren(...replay.calls.map((call, i) => {
      const option = document.createElement('option'); option.value = String(i);
      option.textContent = `${String(call.index).padStart(2, '0')} · ${call.name}`; return option;
    }));
    recordedMode();
  } catch (error) {
    $('demo-status').textContent = `${error.message} The video is still available above.`;
    $('call-counter').textContent = 'Data unavailable';
    $('plan-summary').textContent = 'Recorded plan data is unavailable.';
    $('tool-request').textContent = '—';
    $('task-verdict').textContent = 'Result unavailable';
  }
}
$('play-run').addEventListener('click', async () => {
  if (video.paused) {
    if (video.ended) video.currentTime = 0;
    try { await video.play(); } catch { $('demo-status').textContent = 'Playback could not start. Try the video’s own play control.'; }
  } else video.pause();
});
video.addEventListener('play', () => $('play-run').textContent = 'Pause recording');
video.addEventListener('pause', () => $('play-run').textContent = 'Play recorded run');
video.addEventListener('ended', () => $('play-run').textContent = 'Replay recorded run');
video.addEventListener('error', () => $('demo-status').textContent = 'The video could not be loaded. Driver calls remain available for inspection.');
video.addEventListener('timeupdate', () => {
  if (!replay || live) return;
  const index = replay.calls.findIndex(call => video.currentTime < call.videoEnd - .005);
  const next = index < 0 ? replay.calls.length - 1 : index;
  if (next !== selectedCall) showRecordedCall(next);
});
$('call-picker').addEventListener('change', event => showRecordedCall(Number(event.target.value), true));
$('previous-call').addEventListener('click', () => showRecordedCall(selectedCall - 1, true));
$('next-call').addEventListener('click', () => showRecordedCall(selectedCall + 1, true));
$('playback-speed').addEventListener('change', event => video.playbackRate = Number(event.target.value));

// Progressive enhancement: all robot cases remain readable without JavaScript.
const tablist = document.querySelector('.case-tabs');
const tabs = [...tablist.querySelectorAll('[role="tab"]')];
const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
function selectCase(index, focus = false) {
  panels.forEach((panel, i) => { if (i !== index) panel.querySelectorAll('video').forEach(player => player.pause()); });
  tabs.forEach((tab, i) => { const selected = i === index; tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1; panels[i].hidden = !selected; });
  if (focus) tabs[index].focus();
}
tabs.forEach((tab, index) => {
  panels[index].setAttribute('role', 'tabpanel'); panels[index].setAttribute('aria-labelledby', tab.id); panels[index].tabIndex = 0;
  tab.addEventListener('click', () => selectCase(index));
  tab.addEventListener('keydown', event => {
    const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index - 1 + tabs.length) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
    if (next !== undefined) { event.preventDefault(); selectCase(next, true); }
  });
});
selectCase(0); tablist.hidden = false;

// Each choice remains a direct video link when JavaScript is unavailable.
function updateClipCaption(panel, choice) {
  const livePick = panel.id === 'case-panda' && choice.dataset.execution === 'true' && live;
  panel.querySelector('.clip-title').textContent = livePick ? 'Pick & place · live' : choice.dataset.title;
  panel.querySelector('.clip-caption').textContent = livePick ? $('task-result-description').textContent : choice.dataset.caption;
  panel.querySelector('.clip-attempt').textContent = livePick ? 'Live local simulation' : choice.dataset.attempt;
  const outcome = panel.querySelector('.clip-outcome');
  const liveVerdict = $('task-verdict').textContent;
  outcome.className = `clip-outcome ${livePick ? (liveVerdict === 'Live task: passed' ? 'passed' : liveVerdict === 'Live task: not passed' ? 'not-passed' : 'pending') : choice.dataset.outcome}`;
  outcome.textContent = livePick ? liveVerdict : choice.dataset.resultLabel || (choice.dataset.outcome === 'passed' ? 'Physical task: passed' : 'Physical task: not passed');
  const download = panel.querySelector('.clip-download');
  download.href = choice.getAttribute('href');
  download.hidden = livePick;
}
function syncPandaView() {
  const panel = $('case-panda');
  const choice = panel.querySelector('.clip-choice[aria-current="true"]');
  const inspectExecution = choice.dataset.execution === 'true';
  $('panda-recording').hidden = !inspectExecution;
  $('panda-other-video').hidden = inspectExecution;
  $('panda-execution').hidden = !inspectExecution;
  $('panda-playback').hidden = !inspectExecution;
  $('panda-execution-footer').hidden = !inspectExecution;
  panel.classList.toggle('has-call-trace', inspectExecution);
  video.hidden = live;
  $('live-frame').hidden = !live;
  updateClipCaption(panel, choice);
}
panels.forEach(panel => {
  const player = panel.id === 'case-panda' ? $('panda-other-video') : panel.querySelector('.case-media video');
  const choices = [...panel.querySelectorAll('.clip-choice')];
  choices.forEach(choice => choice.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (choice.getAttribute('aria-current') === 'true') return;
    player.pause();
    choices.forEach(item => item.setAttribute('aria-current', String(item === choice)));
    if (panel.id === 'case-panda') {
      video.pause();
      syncPandaView();
      if (choice.dataset.execution === 'true') return;
    }
    player.src = choice.getAttribute('href');
    player.poster = choice.dataset.poster;
    player.setAttribute('aria-label', `${panel.querySelector('h3').textContent} — ${choice.dataset.title}`);
    player.querySelector('a').href = player.src;
    player.load();
    updateClipCaption(panel, choice);
  }));
});
document.querySelectorAll('video').forEach(player => {
  player.addEventListener('play', () => document.querySelectorAll('video').forEach(other => { if (other !== player) other.pause(); }));
});

async function bridgeRequest(path, method = 'GET') {
  const response = await fetch(`${bridge}${path}`, {
    method, cache: 'no-store', signal: AbortSignal.timeout(6000),
    headers: method === 'POST' ? { 'X-Demo-Token': token } : {}
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Bridge returned ${response.status}.`);
  return result;
}
$('connect-bridge').addEventListener('click', async () => {
  $('connect-bridge').disabled = true;
  $('connection-status').textContent = 'Connecting to the local simulator…';
  try {
    const url = new URL($('bridge-url').value);
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || !['http:', 'https:'].includes(url.protocol)) throw new Error('Use the bridge running on localhost.');
    bridge = url.origin;
    $('open-local-demo').href = `${bridge}/`;
    const status = await bridgeRequest('/api/status');
    if (status.service !== 'autoadapter-local-demo') throw new Error('This is not the Auto Adapter demo bridge.');
    token = status.token;
    $('run-live').disabled = !status.ready || status.running;
    $('connection-status').textContent = status.ready ? 'Connected to local MuJoCo. A live task uses the configured model and its existing usage budget. Source driver validation: 4/5; contact pressing did not pass.' : `Connected, but missing local input: ${status.missing.join(', ')}.`;
    if (status.has_run) { eventCursor = 0; enterLiveMode(); await pollLive(); }
  } catch (error) {
    $('connection-status').textContent = 'The page could not connect to the local bridge. Check that it is running, or use Open local demo below if your browser blocks access from GitHub Pages.';
    $('run-live').disabled = true;
  } finally { $('connect-bridge').disabled = false; }
});
function enterLiveMode() {
  lastLivePlan = ''; currentLiveCall = {}; liveFrameVersion = undefined;
  $('demo-status').replaceChildren();
  document.querySelector('.playback-controls').hidden = true;
  document.querySelector('.call-picker-label').hidden = true;
  $('call-picker').hidden = true;
  live = true; video.pause(); video.hidden = true; $('live-frame').hidden = false;
  $('live-frame').removeAttribute('src'); $('live-frame').alt = 'Waiting for the first real MuJoCo frame';
  $('mode-label').textContent = 'Live local simulation';
  $('task-verdict').textContent = 'Not evaluated yet';
  $('task-result-description').textContent = 'The independent physical check runs after this live task finishes.';
  $('playback-note').textContent = 'Frames come from the actual local MuJoCo run. Motion may finish between browser updates; planning pauses depend on the model.';
  ['play-run', 'call-picker', 'previous-call', 'next-call', 'playback-speed', 'run-live', 'connect-bridge'].forEach(id => $(id).disabled = true);
  $('stop-live').disabled = false; $('call-counter').textContent = 'Waiting for model';
  showCall({}, false);
  syncPandaView();
}
$('run-live').addEventListener('click', async () => {
  $('run-live').disabled = true;
  try { await bridgeRequest('/api/run', 'POST'); eventCursor = 0; enterLiveMode(); await pollLive(); }
  catch (error) { $('connection-status').textContent = error.message; $('run-live').disabled = false; }
});
$('stop-live').addEventListener('click', async () => {
  try { await bridgeRequest('/api/stop', 'POST'); $('connection-status').textContent = 'Stop requested. Waiting for the worker to exit.'; }
  catch (error) { $('connection-status').textContent = error.message; }
});
let lastLivePlan = '', currentLiveCall = {}, liveFrameVersion;
async function pollLive() {
  if (polling) return;
  polling = true;
  try {
    const state = await bridgeRequest(`/api/events?after=${eventCursor}`);
    for (const event of state.events) {
      if (event.type === 'model_start') $('connection-status').textContent = 'ReCAP is asking the model for its next plan…';
      if (event.type === 'plan') { lastLivePlan = event.summary; $('plan-summary').textContent = lastLivePlan; }
      if (event.type === 'call') { currentLiveCall = { name: event.name, request: event.request, planSummary: lastLivePlan }; $('call-counter').textContent = `Live call ${event.index}`; showCall(currentLiveCall, false); $('connection-status').textContent = 'The generated driver is controlling MuJoCo…'; }
      if (event.type === 'result') showCall({ ...currentLiveCall, driverResult: event.driverResult }, false);
    }
    eventCursor = state.cursor;
    if (state.frame !== null && state.frame !== liveFrameVersion) { liveFrameVersion = state.frame; $('live-frame').src = `${bridge}/api/frame?v=${state.frame}`; $('live-frame').alt = 'Actual rendered state from the local MuJoCo run'; }
    if (state.running) setTimeout(pollLive, 350);
    else {
      $('stop-live').disabled = true; $('run-live').disabled = false; $('connect-bridge').disabled = false;
      if (state.report) {
        const passed = state.report.ok === true && state.report.execution_ok === true && state.report.physical_task_success === true;
        $('task-verdict').textContent = passed ? 'Live task: passed' : 'Live task: not passed';
        $('task-result-description').textContent = 'This is a fresh diagnostic demonstration. It does not change the source driver validation or enter a formal experiment denominator.';
        $('connection-status').textContent = passed ? 'The real task completed and passed the independent physical checks.' : 'The run ended without meeting every execution and physical success requirement.';
      } else { $('task-verdict').textContent = 'No completed task verdict'; $('connection-status').textContent = state.error || 'The live worker stopped before producing a final report.'; }
      const back = document.createElement('button'); back.type = 'button'; back.className = 'button secondary'; back.textContent = 'Return to recorded demo';
      back.addEventListener('click', () => { recordedMode(); back.remove(); });
      $('demo-status').replaceChildren(back);
      syncPandaView();
    }
  } catch (error) { $('connection-status').textContent = `Connection lost: ${error.message} The local worker may still be running; reconnect to check or stop it.`; $('connect-bridge').disabled = false; }
  finally { polling = false; }
}
loadReplay();
if (['127.0.0.1', 'localhost'].includes(location.hostname)) $('bridge-url').value = location.origin;
