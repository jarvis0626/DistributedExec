'use strict';
const $ = id => document.getElementById(id);
let editor, selectedJob, logCursor = 0, logText = '', polling = false, submitting = false;
let upload = null, retryKey = null, previewJob = null;
let connected = false, loadingFile = false, fileVersion = 0, jobAction = false, jobStatus = null;
const examples = {
  transform: 'def task(data):\n    return [{"input": item, "square": item * item} for item in data]',
  cpu: 'def task(data):\n    import hashlib\n    return [{"input": x, "digest": hashlib.pbkdf2_hmac("sha256", str(x).encode(), b"DistributedExec", 200000).hex()} for x in data]',
  error: 'def task(data):\n    raise RuntimeError("Intentional example error")',
  long: 'def task(data):\n    import time\n    for second in range(15):\n        print(f"Working: {second + 1}/15", flush=True)\n        time.sleep(1)\n    return [item * 2 for item in data]'
};
const fieldLabels = {
  code: 'Code', dataset: 'Dataset', function_name: 'Function name', chunk_size: 'Items per chunk',
  cpu: 'CPU reservation', memory_mb: 'RAM', timeout: 'Timeout', priority: 'Priority'
};
const pythonKeywords = new Set('False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield'.split(' '));
function csrf() {
  return decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('distributedexec_csrf='))?.split('=')[1] || '');
}
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: {
    'Content-Type': 'application/json', 'X-CSRF-Token': csrf(), ...options.headers
  }});
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = Array.isArray(body.detail) ? body.detail.map(item => {
      const field = item.loc?.at(-1);
      return (fieldLabels[field] || field || 'Request') + ': ' + item.msg;
    }).join('\n') : body.detail;
    const error = new Error(typeof detail === 'string' ? detail : 'Request failed (' + response.status + '). Try again.');
    error.status = response.status;
    throw error;
  }
  return response.json();
}
function message(text, kind = 'info') {
  $('message').textContent = text; $('message').dataset.kind = kind;
}
function readableError(error) {
  return error instanceof TypeError ? 'Could not reach the host. Check that it is running, then try again.' : error.message;
}
function code(value) {
  if (value !== undefined) { $('code').value = value; if (editor) editor.setValue(value); }
  return editor ? editor.getValue() : $('code').value;
}
function syncControls() {
  $('runBtn').disabled = !connected || submitting || loadingFile;
  $('runBtn').textContent = submitting ? 'Submitting...' : loadingFile ? 'Reading dataset...' : 'Distribute & execute';
  $('cancel').disabled = !connected || jobAction || !['queued', 'running'].includes(jobStatus);
  $('retry').disabled = !connected || jobAction || !['failed', 'cancelled'].includes(jobStatus);
  $('clearUpload').disabled = upload === null && !loadingFile;
  document.querySelectorAll('.revoke-worker').forEach(button => { button.disabled = !connected || button.dataset.pending === 'true'; });
}
function clearValidation() {
  $('validationError').hidden = true;
  document.querySelectorAll('[aria-invalid="true"]').forEach(element => element.removeAttribute('aria-invalid'));
}
function invalid(id, text) {
  const field = $(id); field.setAttribute('aria-invalid', 'true');
  const details = field.closest('details'); if (details) details.open = true;
  $('validationError').textContent = text; $('validationError').hidden = false;
  if (id === 'code' && editor) editor.focus(); else field.focus();
  message(text, 'error');
  return null;
}
function parseDataset(text) {
  let value;
  try { value = JSON.parse(text); }
  catch { throw new Error('Use valid JSON, for example [1, 2, 3]. Check commas, brackets and double quotes.'); }
  if (!Array.isArray(value)) throw new Error('Dataset must be a JSON array, for example [1, 2, 3].');
  // JSON.parse accepts overflowing numeric literals; JSON.stringify would silently turn them into null.
  const pending = [value];
  while (pending.length) {
    const item = pending.pop();
    if (typeof item === 'number' && !Number.isFinite(item)) throw new Error('Dataset numbers must be finite. A number such as 1e400 is too large.');
    if (item && typeof item === 'object') for (const child of Object.values(item)) pending.push(child);
  }
  return value;
}
function updateDatasetSummary() {
  try {
    const dataset = upload ?? parseDataset($('dataset').value), size = Number($('chunkSize').value);
    const chunks = size >= 1 && Number.isInteger(size) ? Math.ceil(dataset.length / size) : null;
    $('datasetSummary').textContent = dataset.length === 0 ?
      'Empty array: completes immediately with an empty result. Your function will not run.' :
      dataset.length.toLocaleString() + ' items' + (chunks === null ? '' : ' in ' + chunks.toLocaleString() + ' chunk' + (chunks === 1 ? '' : 's')) + '. Results keep the original chunk order.';
  } catch { $('datasetSummary').textContent = 'Enter a JSON array, for example [1, 2, 3], or choose a file.'; }
}
function validatePayload() {
  clearValidation();
  const source = code();
  if (!source.trim()) return invalid('code', 'Add a Python function before submitting.');
  if (source.length > 262144) return invalid('code', 'Code must be no longer than 262,144 characters.');
  const functionName = $('funcName').value.trim();
  if (!/^[_\p{ID_Start}][\p{ID_Continue}]*$/u.test(functionName) || pythonKeywords.has(functionName) || functionName.startsWith('__')) {
    return invalid('funcName', 'Use a Python function name such as task, matching the function defined in your code.');
  }
  let dataset;
  try { dataset = upload ?? parseDataset($('dataset').value); }
  catch (error) { return invalid('dataset', error.message); }
  const numericFields = { chunkSize: 'Items per chunk', cpu: 'CPU reservation', memory: 'RAM', timeout: 'Timeout', priority: 'Priority' };
  for (const [id, label] of Object.entries(numericFields)) {
    const field = $(id), value = Number(field.value);
    if (field.value === '' || !Number.isFinite(value) || value < Number(field.min) || value > Number(field.max) || (id !== 'cpu' && !Number.isInteger(value))) {
      return invalid(id, label + ' must be ' + (id === 'cpu' ? 'a number' : 'a whole number') + ' between ' + field.min + ' and ' + field.max + '.');
    }
  }
  return { code: source, dataset, function_name: functionName, chunk_size: Number($('chunkSize').value),
    cpu: Number($('cpu').value), memory_mb: Number($('memory').value), timeout: Number($('timeout').value), priority: Number($('priority').value) };
}
code(examples.transform);
if (typeof require === 'function') {
  require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
  require(['vs/editor/editor.main'], () => {
    $('editorContainer').hidden = false;
    editor = monaco.editor.create($('editorContainer'), { value: $('code').value, language: 'python', theme: 'vs-dark',
      ariaLabel: 'Python function code', minimap: { enabled: false }, automaticLayout: true });
    editor.onDidChangeModelContent(clearValidation); $('code').hidden = true;
  }, () => { message('The plain text editor is ready. You can still edit and submit Python code.'); });
}
$('jobForm').addEventListener('input', clearValidation);
$('example').addEventListener('change', () => { code(examples[$('example').value]); $('funcName').value = 'task'; clearValidation(); });
$('preset').addEventListener('change', () => {
  const settings = { small: [1, 256], balanced: [2, 512], large: [4, 1024] }[$('preset').value];
  if (settings) [$('cpu').value, $('memory').value] = settings;
  clearValidation();
});
for (const id of ['cpu', 'memory']) $(id).addEventListener('input', () => { $('preset').value = 'custom'; });
for (const id of ['dataset', 'chunkSize']) $(id).addEventListener('input', updateDatasetSummary);
function resetUpload() {
  ++fileVersion; loadingFile = false; upload = null; $('dataset').disabled = false; $('fileInput').value = '';
  $('dropZoneText').textContent = 'Choose or drop a JSON array file (up to 15 MiB)';
  $('dropZone').classList.remove('has-file'); clearValidation(); updateDatasetSummary(); syncControls();
}
$('clearUpload').addEventListener('click', () => { resetUpload(); message('File cleared. The editable array will be used for your next job.'); });
async function loadFile(file) {
  if (!file) return;
  const version = ++fileVersion;
  loadingFile = true; syncControls(); clearValidation(); $('dropZoneText').textContent = 'Reading ' + file.name + '...';
  try {
    if (file.size > 15 * 1024 * 1024) throw new Error('Dataset file exceeds 15 MiB. Choose a smaller JSON array file.');
    const value = parseDataset(await file.text());
    if (version !== fileVersion) return;
    upload = value; $('dataset').disabled = true;
    $('dropZoneText').textContent = file.name + ': ' + value.length.toLocaleString() + ' items. Clear file to resume manual editing.';
    $('dropZone').classList.add('has-file'); message('Loaded ' + file.name + '. This file will be used for your next job.', 'success');
  } catch (error) {
    if (version !== fileVersion) return;
    resetUpload(); message(file.name + ' was not loaded. ' + error.message + ' The editable array is active.', 'error');
  } finally {
    if (version === fileVersion) { loadingFile = false; updateDatasetSummary(); syncControls(); }
  }
}
$('chooseFile').addEventListener('click', () => $('fileInput').click());
$('fileInput').addEventListener('change', event => loadFile(event.target.files[0]));
for (const eventName of ['dragenter', 'dragover', 'dragleave', 'drop']) {
  $('dropZone').addEventListener(eventName, event => {
    event.preventDefault(); event.stopPropagation();
    $('dropZone').classList.toggle('dragover', eventName === 'dragenter' || eventName === 'dragover');
    if (eventName === 'drop') loadFile(event.dataTransfer.files[0]);
  });
}
$('jobForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (submitting || loadingFile || !connected) return;
  const payload = validatePayload(); if (!payload) return;
  submitting = true; $('jobForm').setAttribute('aria-busy', 'true'); syncControls();
  const key = crypto.randomUUID();
  try {
    // Retry a transient network failure with the same payload and idempotency key.
    let result;
    const options = { method: 'POST', body: JSON.stringify(payload), headers: { 'Idempotency-Key': key } };
    try { result = await api('/api/run', options); }
    catch (error) { if (!(error instanceof TypeError)) throw error; result = await api('/api/run', options); }
    selectJob(result.job_id);
    message(payload.dataset.length ? 'Job submitted. Watch its progress below; queued chunks start when a worker is ready.' : 'Empty dataset completed. Download the empty result below.', 'success');
    await poll(); $('detail').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  } catch (error) { message(readableError(error), 'error'); }
  finally { submitting = false; $('jobForm').removeAttribute('aria-busy'); syncControls(); }
});
function selectJob(id) {
  if (selectedJob === id) return;
  selectedJob = id; logCursor = 0; logText = ''; retryKey = crypto.randomUUID(); previewJob = null; jobStatus = null;
  $('preview').textContent = ''; $('logs').textContent = 'Loading logs...'; $('detail').hidden = false;
  $('jobTitle').textContent = 'Job ' + id.slice(0, 8) + ' - loading...';
  $('error').hidden = true; $('queuedReason').hidden = true; $('download').hidden = true; $('retryHelp').hidden = true;
  $('progress').value = 0; $('progressText').textContent = 'Loading job details...'; $('attempts').replaceChildren(); syncControls();
}
function node(tag, text, className) {
  const element = document.createElement(tag); element.textContent = text;
  if (className) element.className = className; return element;
}
function duration(start, end) { return Math.max(0, end - start).toFixed(1) + 's'; }
function reconcile(container, items, render) {
  const existing = new Map([...container.children].map(element => [element.dataset.id, element]));
  const ids = new Set(items.map(item => item.id));
  for (const element of [...container.children]) if (!ids.has(element.dataset.id)) element.remove();
  items.forEach((item, index) => {
    const element = render(item, existing.get(item.id)); element.dataset.id = item.id;
    if (container.children[index] !== element) container.insertBefore(element, container.children[index] || null);
  });
}
function showStatus(state) {
  const online = state.workers.filter(worker => worker.online), ready = online.filter(worker => worker.mode === 'accepting' && worker.runtime_id);
  $('workerCount').textContent = ready.length + ' ready / ' + online.length + ' online';
  $('workerBadge').classList.toggle('offline', ready.length === 0); $('workerHint').hidden = ready.length !== 0;
  $('workerHint').textContent = online.length ?
    'No workers are ready to accept jobs. Resume a paused worker or prepare its Docker runtime in the desktop launcher. New jobs will wait in the queue.' :
    'No workers are connected. Join a worker from the desktop launcher to run non-empty datasets. You can submit now; your job will wait in the queue.';
  reconcile($('jobs'), state.jobs, (job, button) => {
    if (!button) {
      button = node('button', '', 'job'); button.type = 'button';
      const heading = node('span', '', 'job-heading'); heading.append(node('strong', ''), node('span', '', 'state'));
      button.append(heading, node('span', '', 'job-meta'));
      button.addEventListener('click', () => { selectJob(job.id); poll(); });
    }
    button.querySelector('strong').textContent = job.id.slice(0, 8);
    const status = button.querySelector('.state'); status.textContent = job.status; status.className = 'state ' + job.status;
    button.querySelector('.job-meta').textContent = job.completed_chunks + '/' + job.total_chunks + ' chunks - total ' + duration(job.created, job.finished || state.server_time) + ' - priority ' + job.priority;
    button.classList.toggle('selected', selectedJob === job.id); button.setAttribute('aria-pressed', String(selectedJob === job.id)); return button;
  });
  if (!state.jobs.length) $('jobs').replaceChildren(node('p', 'No jobs yet. Start with the transformation example.', 'muted'));
  reconcile($('workers'), state.workers, (worker, item) => {
    if (!item) {
      item = node('div', '', 'worker'); item.append(node('strong', ''), node('p', '', 'muted'));
      const revoke = node('button', 'Revoke access', 'btn small danger revoke-worker'); revoke.type = 'button';
      revoke.title = 'Disconnect this worker and require a new pairing code to reconnect.';
      revoke.addEventListener('click', async () => {
        if (!confirm('Revoke access for "' + worker.name + '"? Any running chunks will be reassigned. This computer must pair again to reconnect.')) return;
        revoke.dataset.pending = 'true'; syncControls();
        try {
          await api('/api/workers/' + worker.id + '/revoke', { method: 'POST' });
          message('Access revoked for ' + worker.name + '. Pair this computer again if you want to reconnect it.', 'success'); await poll();
        } catch (error) { message(readableError(error), 'error'); }
        finally { delete revoke.dataset.pending; syncControls(); }
      }); item.append(revoke);
    }
    item.querySelector('strong').textContent = worker.name + ' - ' + (worker.revoked ? 'revoked' : worker.online ? worker.mode : 'offline');
    item.querySelector('p').textContent = 'Last seen ' + worker.heartbeat_age.toFixed(1) + 's ago - ' + worker.active_attempts.length + ' active attempts\nReserved: ' + worker.reserved_cpu + '/' + worker.cpu + ' CPU, ' + worker.reserved_memory_mb + '/' + worker.memory_mb + ' MiB RAM\nRuntime: ' + (worker.runtime_id ? worker.runtime_id.slice(0, 24) : 'not ready - prepare runtime in the launcher');
    item.querySelector('button').hidden = Boolean(worker.revoked); return item;
  });
  if (!state.workers.length) $('workers').replaceChildren(node('p', 'No computers paired yet. Prepare a worker runtime, then choose Join as a worker in its desktop launcher.', 'muted'));
  syncControls();
}
async function showJob() {
  if (!selectedJob) return;
  const id = selectedJob, job = await api('/api/jobs/' + id);
  if (id !== selectedJob) return;
  jobStatus = job.status; $('detail').hidden = false; $('jobTitle').textContent = 'Job ' + job.id.slice(0, 8) + ' - ' + job.status;
  $('progress').max = job.total_chunks || 1; $('progress').value = job.total_chunks ? job.completed_chunks : (job.status === 'succeeded' ? 1 : 0);
  const running = job.chunks.filter(chunk => chunk.state === 'running').length, queued = job.chunks.filter(chunk => chunk.state === 'queued').length;
  $('progressText').textContent = job.total_chunks ?
    job.completed_chunks + '/' + job.total_chunks + ' chunks completed' + (running ? ' - ' + running + ' running' : '') + (queued ? ' - ' + queued + ' queued' : '') :
    'Empty dataset completed without running your function.';
  $('queuedReason').textContent = job.queued_reason || ''; $('queuedReason').hidden = !job.queued_reason;
  $('error').textContent = job.error || ''; $('error').hidden = !job.error;
  $('retryHelp').hidden = !['failed', 'cancelled'].includes(job.status);
  $('download').hidden = job.status !== 'succeeded'; $('download').href = '/api/jobs/' + id + '/result'; syncControls();
  if (job.status === 'succeeded' && previewJob !== id) {
    const preview = await api('/api/jobs/' + id + '/preview'); if (id !== selectedJob) return;
    $('preview').textContent = preview.total_items + ' output items. Preview (first 10):\n' + JSON.stringify(preview.items, null, 2); previewJob = id;
  }
  $('attempts').replaceChildren();
  const attemptsByChunk = new Map();
  for (const attempt of job.attempts) {
    if (!attemptsByChunk.has(attempt.chunk_id)) attemptsByChunk.set(attempt.chunk_id, []);
    attemptsByChunk.get(attempt.chunk_id).push(attempt);
  }
  for (const chunk of job.chunks) {
    const attempts = attemptsByChunk.get(chunk.id) || [];
    for (const attempt of attempts.length ? attempts : [null]) {
      const row = document.createElement('tr');
      const values = [chunk.ordinal + 1, chunk.state, attempt?.worker_name || 'Unassigned', attempt?.state || 'Queued', attempt?.runtime_id?.slice(0, 16) || '-', attempt?.error || ''];
      values.forEach(value => row.append(node('td', String(value)))); $('attempts').append(row);
    }
  }
  if (!job.chunks.length) {
    const cell = node('td', 'No chunks: this job used an empty dataset.'); cell.colSpan = 6;
    const row = node('tr', ''); row.append(cell); $('attempts').append(row);
  }
  const logs = await api('/api/jobs/' + id + '/logs?after=' + logCursor); if (id !== selectedJob) return;
  for (const log of logs) { logCursor = log.cursor; logText += '[' + log.attempt_id.slice(0, 6) + ' ' + log.stream + ' #' + log.seq + '] ' + log.text; }
  if (logText.length > 262144) logText = '[Earlier logs omitted from this view]\n' + logText.slice(-250000);
  $('logs').textContent = logText || 'No logs yet. Use print(..., flush=True) in your function to see output here.';
}
for (const action of ['cancel', 'retry']) {
  $(action).addEventListener('click', async () => {
    if (jobAction || !selectedJob || !connected) return;
    jobAction = true; syncControls(); const id = selectedJob;
    try {
      const result = await api('/api/jobs/' + id + '/' + action, { method: 'POST', headers: { 'Idempotency-Key': retryKey } });
      if (result.job_id) selectJob(result.job_id);
      const feedback = action === 'retry' ? 'Retry submitted as a new job using the original code, dataset and settings.' :
        result.status === 'cancelled' ? 'Job ' + id.slice(0, 8) + ' cancelled.' : 'Job ' + id.slice(0, 8) + ' already finished with status ' + result.status + '.';
      message(feedback, 'success'); await poll();
    } catch (error) { message(readableError(error), 'error'); }
    finally { jobAction = false; syncControls(); }
  });
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await api('/api/status'); connected = true;
    $('workspace').hidden = false; $('onboarding').hidden = true; $('connectionIssue').hidden = true;
    $('connection').textContent = 'Connected - updates every 1.5 seconds'; showStatus(state); await showJob();
  } catch (error) {
    connected = false; $('connectionIssue').hidden = false;
    const signIn = error.status === 401 || error.status === 403;
    $('connection').textContent = signIn ? 'Sign-in required' : 'Connection lost - retrying automatically';
    $('connectionHelp').textContent = signIn ?
      'Open a fresh dashboard from the desktop launcher to sign in again. Your submitted jobs stay in the workspace.' :
      'The host is unreachable. Check that it is running and your network is connected. This page will reconnect automatically; displayed progress may be out of date.';
    if (signIn) { $('workspace').hidden = true; $('onboarding').hidden = false; } syncControls();
  } finally { polling = false; }
}
$('reconnect').addEventListener('click', poll);
async function bootstrap() {
  const ticket = new URLSearchParams(location.hash.slice(1)).get('ticket'); history.replaceState(null, '', location.pathname);
  if (ticket) {
    try { await api('/api/session', { method: 'POST', body: JSON.stringify({ ticket }) }); }
    catch (error) { message(readableError(error) + '. Open a fresh dashboard from the desktop launcher.', 'error'); }
  }
  updateDatasetSummary(); await poll(); setInterval(poll, 1500);
}
bootstrap();
