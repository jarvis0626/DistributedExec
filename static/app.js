'use strict';
const $ = id => document.getElementById(id);
let editor, selectedJob, logCursor = 0, logText = '', polling = false, submitting = false;
let upload = null, retryKey = null;
let previewJob = null;
const examples = {
  transform: 'def task(data):\n    return [{"input": item, "square": item * item} for item in data]',
  cpu: 'def task(data):\n    import hashlib\n    return [{"input": x, "digest": hashlib.pbkdf2_hmac("sha256", str(x).encode(), b"DistributedExec", 200000).hex()} for x in data]',
  error: 'def task(data):\n    raise RuntimeError("Intentional example error")',
  long: 'def task(data):\n    import time\n    for second in range(15):\n        print(f"Working: {second + 1}/15", flush=True)\n        time.sleep(1)\n    return [item * 2 for item in data]'
};
function csrf() {
  return decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('distributedexec_csrf='))?.split('=')[1] || '');
}
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers: {
    'Content-Type': 'application/json', 'X-CSRF-Token': csrf(), ...options.headers
  }});
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail));
  }
  return response.json();
}
function message(text) { $('message').textContent = text; }
function code(value) {
  if (value !== undefined) { $('code').value = value; if (editor) editor.setValue(value); }
  return editor ? editor.getValue() : $('code').value;
}
code(examples.transform);
if (typeof require === 'function') {
  require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
  require(['vs/editor/editor.main'], () => {
    $('editorContainer').hidden = false;
    editor = monaco.editor.create($('editorContainer'), { value: $('code').value, language: 'python', theme: 'vs-dark',
      minimap: { enabled: false }, automaticLayout: true });
    $('code').hidden = true;
  }, () => { message('The plain text editor is available while Monaco is unavailable.'); });
}
$('example').addEventListener('change', () => code(examples[$('example').value]));
$('preset').addEventListener('change', () => {
  const settings = { small: [1, 256], balanced: [2, 512], large: [4, 1024] }[$('preset').value];
  [$('cpu').value, $('memory').value] = settings;
});
function resetUpload() {
  upload = null; $('dataset').disabled = false; $('fileInput').value = '';
  $('dropZoneText').textContent = 'Choose or drop a JSON array file'; $('dropZone').classList.remove('has-file');
}
$('clearUpload').addEventListener('click', resetUpload);
async function loadFile(file) {
  if (!file) return;
  try {
    if (file.size > 15 * 1024 * 1024) throw new Error('Dataset file exceeds 15 MiB');
    const value = JSON.parse(await file.text());
    if (!Array.isArray(value)) throw new Error('The file must contain a JSON array');
    upload = value; $('dataset').disabled = true;
    $('dropZoneText').textContent = `${file.name}: ${value.length} items. Clear file to resume manual editing.`;
    $('dropZone').classList.add('has-file');
  } catch (error) { message(error.message); }
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
$('runBtn').addEventListener('click', async () => {
  if (submitting) return;
  submitting = true; $('runBtn').disabled = true;
  const key = crypto.randomUUID();
  try {
    const dataset = upload ?? JSON.parse($('dataset').value);
    if (!Array.isArray(dataset)) throw new Error('Dataset must be a JSON array');
    const payload = { code: code(), dataset, function_name: $('funcName').value, chunk_size: Number($('chunkSize').value),
      cpu: Number($('cpu').value), memory_mb: Number($('memory').value), timeout: Number($('timeout').value), priority: Number($('priority').value) };
    // Retry one transient network failure with an unchanged payload and idempotency key.
    let result;
    const options = { method: 'POST', body: JSON.stringify(payload), headers: { 'Idempotency-Key': key } };
    try { result = await api('/api/run', options); }
    catch (error) { if (!(error instanceof TypeError)) throw error; result = await api('/api/run', options); }
    selectJob(result.job_id); message('Job submitted. Workers can join while chunks remain queued.');
    await poll();
  } catch (error) { message(error.message); }
  finally { submitting = false; $('runBtn').disabled = false; }
});
function selectJob(id) {
  if (selectedJob !== id) { selectedJob = id; logCursor = 0; logText = ''; retryKey = crypto.randomUUID(); previewJob = null; $('preview').textContent = ''; }
}
function node(tag, text, className) {
  const element = document.createElement(tag); element.textContent = text;
  if (className) element.className = className;
  return element;
}
function duration(start, end) { return `${Math.max(0, end - start).toFixed(1)}s`; }
function showStatus(state) {
  const online = state.workers.filter(worker => worker.online);
  $('workerCount').textContent = `${online.length} workers online`;
  $('workerBadge').classList.toggle('offline', online.length === 0);
  $('jobs').replaceChildren();
  if (!state.jobs.length) $('jobs').append(node('p', 'No jobs yet. Try the transformation example.', 'muted'));
  for (const job of state.jobs) {
    const button = node('button', `${job.id.slice(0, 8)} · ${job.status} · ${job.completed_chunks}/${job.total_chunks} chunks\nPriority ${job.priority} · total ${duration(job.created, job.finished || state.server_time)}`, 'job');
    button.classList.toggle('selected', selectedJob === job.id);
    button.addEventListener('click', () => { selectJob(job.id); poll(); }); $('jobs').append(button);
  }
  $('workers').replaceChildren();
  if (!state.workers.length) $('workers').append(node('p', 'No computers paired. Open DistributedExec on a worker computer and choose Join as a worker. Prepare its runtime first.', 'muted'));
  for (const worker of state.workers) {
    const item = node('div', '', 'worker');
    item.append(node('strong', `${worker.name} · ${worker.revoked ? 'revoked' : worker.online ? worker.mode : 'offline'}`));
    item.append(node('p', `Heartbeat ${worker.heartbeat_age.toFixed(1)}s ago · ${worker.active_attempts.length} active attempts\nReserved: ${worker.reserved_cpu}/${worker.cpu} CPU, ${worker.reserved_memory_mb}/${worker.memory_mb} MiB RAM\nRuntime: ${worker.runtime_id ? worker.runtime_id.slice(0, 24) : 'not ready'}`, 'muted'));
    if (!worker.revoked) {
      const revoke = node('button', 'Revoke', 'btn small danger');
      revoke.addEventListener('click', async () => { try { await api(`/api/workers/${worker.id}/revoke`, { method: 'POST' }); await poll(); } catch (error) { message(error.message); } });
      item.append(revoke);
    }
    $('workers').append(item);
  }
}
async function showJob() {
  if (!selectedJob) return;
  const id = selectedJob;
  const job = await api(`/api/jobs/${id}`);
  if (id !== selectedJob) return;
  $('detail').hidden = false;
  $('jobTitle').textContent = `Job ${job.id.slice(0, 8)} · ${job.status}`;
  $('progress').max = job.total_chunks || 1;
  $('progress').value = job.total_chunks ? job.completed_chunks : (job.status === 'succeeded' ? 1 : 0);
  $('progressText').textContent = `${job.completed_chunks}/${job.total_chunks} accepted chunks`;
  $('queuedReason').textContent = job.queued_reason || '';
  $('error').textContent = job.error || '';
  $('cancel').disabled = !['queued', 'running'].includes(job.status);
  $('retry').disabled = !['failed', 'cancelled'].includes(job.status);
  $('download').hidden = job.status !== 'succeeded';
  $('download').href = `/api/jobs/${id}/result`;
  if (job.status === 'succeeded' && previewJob !== id) {
    const preview = await api(`/api/jobs/${id}/preview`);
    if (id !== selectedJob) return;
    $('preview').textContent = `${preview.total_items} output items. Preview (first 10):\n${JSON.stringify(preview.items, null, 2)}`;
    previewJob = id;
  }
  $('attempts').replaceChildren();
  for (const chunk of job.chunks) {
    const attempts = job.attempts.filter(attempt => attempt.chunk_id === chunk.id);
    for (const attempt of attempts.length ? attempts : [null]) {
      const row = document.createElement('tr');
      const values = [chunk.ordinal, chunk.state, attempt?.worker_name || 'Unassigned', attempt?.state || 'Queued',
        attempt?.runtime_id?.slice(0, 16) || '—', attempt?.error || ''];
      values.forEach(value => row.append(node('td', String(value)))); $('attempts').append(row);
    }
  }
  const logs = await api(`/api/jobs/${id}/logs?after=${logCursor}`);
  if (id !== selectedJob) return;
  for (const log of logs) { logCursor = log.cursor; logText += `[${log.attempt_id.slice(0, 6)} ${log.stream} #${log.seq}] ${log.text}`; }
  if (logText.length > 262144) logText = '[Earlier logs omitted from this view]\n' + logText.slice(-250000);
  $('logs').textContent = logText || 'No logs yet.';
}
for (const action of ['cancel', 'retry']) {
  $(action).addEventListener('click', async () => {
    $(action).disabled = true;
    try {
      const result = await api(`/api/jobs/${selectedJob}/${action}`, { method: 'POST', headers: { 'Idempotency-Key': retryKey } });
      if (result.job_id) selectJob(result.job_id);
      await poll();
    } catch (error) { message(error.message); }
  });
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await api('/api/status');
    $('workspace').hidden = false; $('onboarding').hidden = true;
    $('connection').textContent = 'Connected to workspace';
    showStatus(state); await showJob();
  } catch (error) { $('connection').textContent = 'Disconnected or sign-in required'; message(error.message); }
  finally { polling = false; }
}
async function bootstrap() {
  const ticket = new URLSearchParams(location.hash.slice(1)).get('ticket');
  history.replaceState(null, '', location.pathname);
  if (ticket) {
    try { await api('/api/session', { method: 'POST', body: JSON.stringify({ ticket }) }); }
    catch (error) { message(error.message); }
  }
  await poll(); setInterval(poll, 1500);
}
bootstrap();
