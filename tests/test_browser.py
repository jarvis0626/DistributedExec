import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest
import requests
from distributedexec.credentials import admin_secret

pytestmark = pytest.mark.browser


@pytest.fixture
def browser_host(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    edge = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    browser_path = os.environ.get('DISTRIBUTEDEXEC_BROWSER') or (str(edge) if edge.exists() else None)
    if not browser_path:
        pytest.skip('Set DISTRIBUTEDEXEC_BROWSER to an installed Chromium browser executable')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    workspace = tmp_path / 'workspace'
    token = admin_secret(workspace)
    process = subprocess.Popen([sys.executable, 'host.py', '--workspace', str(workspace), '--bind', '127.0.0.1', '--port', str(port)],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        for _ in range(100):
            try:
                requests.get(url + '/health', timeout=.5).raise_for_status()
                break
            except requests.RequestException:
                time.sleep(.1)
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=browser_path, headless=True)
            try:
                yield browser, url, token
            finally:
                browser.close()
    finally:
        try:
            requests.post(url + '/api/desktop/shutdown', headers={'Authorization': 'Bearer ' + token}, timeout=2)
            process.wait(timeout=8)
        except (requests.RequestException, subprocess.TimeoutExpired):
            process.terminate(); process.wait(timeout=5)


def open_authenticated(browser, url, token, fallback=False):
    context = browser.new_context()
    page = context.new_page()
    external = []
    def route(request):
        if not request.request.url.startswith(url):
            external.append(request.request.url); request.abort()
        elif fallback and request.request.url.endswith('/loader.js'):
            request.abort()
        else:
            request.continue_()
    page.route('**/*', route)
    ticket = requests.post(url + '/api/desktop/ticket', headers={'Authorization': 'Bearer ' + token}, timeout=3).json()['ticket']
    page.goto(url + '/#ticket=' + ticket)
    page.wait_for_selector('#workspace', state='visible')
    return context, page, external


def test_offline_editor_upload_clear_and_empty_download(browser_host):
    browser, url, token = browser_host
    context, page, external = open_authenticated(browser, url, token)
    try:
        page.wait_for_selector('.monaco-editor')
        assert page.evaluate('location.hash') == ''
        page.locator('#fileInput').set_input_files({'name': 'input.json', 'mimeType': 'application/json', 'buffer': b'[8,9]'})
        page.wait_for_function('document.getElementById("dataset").disabled')
        page.locator('#clearUpload').click()
        assert page.locator('#dataset').is_enabled()
        page.locator('#dataset').fill('[]')
        page.locator('#runBtn').click()
        page.wait_for_selector('#download', state='visible')
        assert '0 output items' in page.locator('#preview').inner_text()
        with page.expect_download() as event:
            page.locator('#download').click()
        download = event.value
        assert Path(download.path()).read_text(encoding='utf-8') == '[]'
        assert external == []
    finally:
        context.close()


def test_plain_editor_fallback_and_unauthenticated_onboarding(browser_host):
    browser, url, token = browser_host
    context, page, external = open_authenticated(browser, url, token, fallback=True)
    try:
        assert page.locator('#code').is_visible()
        page.locator('#code').fill('def task(data): return data')
        page.locator('#dataset').fill('[1,2]')
        page.locator('#runBtn').click()
        page.wait_for_selector('#detail', state='visible')
        page.wait_for_function("document.getElementById('jobTitle').textContent.includes('queued')")
        assert external == []
    finally:
        context.close()
    context = browser.new_context()
    page = context.new_page(); page.goto(url)
    assert page.locator('#onboarding').is_visible()
    assert page.locator('#workspace').is_hidden()
    context.close()


def test_validation_prevents_invalid_or_silently_changed_jobs(browser_host):
    browser, url, token = browser_host
    context, page, _ = open_authenticated(browser, url, token, fallback=True)
    submissions, script_errors = [], []
    page.on('request', lambda request: submissions.append(request) if request.url.endswith('/api/run') else None)
    page.on('pageerror', lambda error: script_errors.append(str(error)))
    try:
        for value, expected in [('{', 'valid JSON'), ('{"items": [1]}', 'JSON array'), ('[{"nested": [1e400]}]', 'finite')]:
            page.locator('#dataset').fill(value)
            page.locator('#runBtn').click()
            assert expected in page.locator('#validationError').inner_text()
            assert page.locator('#dataset').get_attribute('aria-invalid') == 'true'
        page.locator('#dataset').fill('[1, 2]')
        page.locator('#funcName').fill('def')
        page.locator('#runBtn').click()
        assert 'function name' in page.locator('#validationError').inner_text()
        page.locator('#funcName').fill('task')
        page.locator('#code').fill(' ')
        page.locator('#runBtn').click()
        assert 'Python function' in page.locator('#validationError').inner_text()
        page.locator('#code').fill('def task(data): return data')
        page.locator('#chunkSize').evaluate('(element) => element.value = "0"')
        page.locator('#runBtn').click()
        assert page.locator('#advancedSettings').get_attribute('open') is not None
        assert 'whole number between 1 and 1000000' in page.locator('#validationError').inner_text()
        page.locator('#chunkSize').fill('1.5')
        page.locator('#runBtn').click()
        assert 'whole number' in page.locator('#validationError').inner_text()
        assert submissions == []
        page.locator('#chunkSize').fill('2')
        page.locator('#dataset').fill('[]')
        assert 'will not run' in page.locator('#datasetSummary').inner_text()
        page.locator('#funcName').press('Enter')
        page.wait_for_selector('#download', state='visible')
        assert len(submissions) == 1
        assert script_errors == []
    finally:
        context.close()


def test_invalid_replacement_upload_returns_to_manual_dataset(browser_host):
    browser, url, token = browser_host
    context, page, _ = open_authenticated(browser, url, token, fallback=True)
    try:
        page.locator('#fileInput').set_input_files({'name': 'valid.json', 'mimeType': 'application/json', 'buffer': b'[8,9]'})
        page.wait_for_function('document.getElementById("dataset").disabled')
        for content in (b'{broken}', b'[{"nested": 1e400}]'):
            page.locator('#fileInput').set_input_files({'name': 'invalid.json', 'mimeType': 'application/json', 'buffer': content})
            page.wait_for_function('!document.getElementById("dataset").disabled')
            assert 'was not loaded' in page.locator('#message').inner_text()
            assert page.locator('#clearUpload').is_disabled()
            assert page.locator('#runBtn').is_enabled()
        page.locator('#dataset').fill('[]')
        page.locator('#runBtn').click()
        page.wait_for_selector('#download', state='visible')
        assert '0 output items' in page.locator('#preview').inner_text()
    finally:
        context.close()


def test_queue_cancel_retry_and_history_preserve_original_job(browser_host):
    browser, url, token = browser_host
    context, page, _ = open_authenticated(browser, url, token, fallback=True)
    try:
        assert 'No workers are connected' in page.locator('#workerHint').inner_text()
        page.locator('#dataset').fill('[1, 2, 3]')
        page.locator('#advancedSettings summary').click()
        page.locator('#chunkSize').fill('1')
        page.locator('#runBtn').click()
        page.wait_for_function("document.getElementById('jobTitle').textContent.includes('queued')")
        assert 'worker to connect' in page.locator('#queuedReason').inner_text()
        assert '3 queued' in page.locator('#progressText').inner_text()
        page.locator('#cancel').click()
        page.wait_for_function("document.getElementById('jobTitle').textContent.includes('cancelled')")
        assert page.locator('#retryHelp').is_visible()
        page.locator('#dataset').fill('[]')
        page.locator('#code').fill('def task(data): raise RuntimeError("edited")')
        page.locator('#retry').click()
        page.wait_for_function("document.getElementById('jobTitle').textContent.includes('queued')")
        assert '3 queued' in page.locator('#progressText').inner_text()
        response = requests.get(url + '/api/status', headers={'Authorization': 'Bearer ' + token}, timeout=3)
        jobs = response.json()['jobs']
        assert len(jobs) == 2
        assert jobs[0]['parent_id'] == jobs[1]['id']
        assert jobs[0]['total_chunks'] == 3
        # Polling updates existing history controls so keyboard focus survives updates.
        selected = page.locator('.job.selected')
        selected.focus()
        page.evaluate('poll()')
        assert selected.evaluate('(element) => element === document.activeElement')
    finally:
        context.close()


def test_connection_loss_and_session_expiry_have_actionable_recovery(browser_host):
    browser, url, token = browser_host
    context, page, _ = open_authenticated(browser, url, token, fallback=True)
    try:
        page.route('**/api/status', lambda route: route.abort())
        page.wait_for_selector('#connectionIssue', state='visible')
        assert 'host is unreachable' in page.locator('#connectionHelp').inner_text()
        assert page.locator('#runBtn').is_disabled()
        page.unroute('**/api/status')
        page.locator('#reconnect').click()
        page.wait_for_selector('#connectionIssue', state='hidden')
        assert page.locator('#runBtn').is_enabled()
        context.clear_cookies()
        page.wait_for_selector('#workspace', state='hidden')
        assert page.locator('#onboarding').is_visible()
        assert 'fresh dashboard' in page.locator('#connectionHelp').inner_text()
    finally:
        context.close()


def test_mobile_layout_and_labels_remain_usable(browser_host):
    browser, url, token = browser_host
    context, page, _ = open_authenticated(browser, url, token, fallback=True)
    try:
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.get_by_label('Function name', exact=True).fill('task')
        page.get_by_label('Or edit an array', exact=True).fill('[1, 2]')
        page.get_by_role('button', name='Distribute & execute').click()
        page.wait_for_function("document.getElementById('jobTitle').textContent.includes('queued')")
        page.locator('.job-diagnostics summary').first.click()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert page.locator('#cancel').is_visible()
        assert page.get_by_role('progressbar', name='Completed chunks').is_visible()
    finally:
        context.close()


@pytest.fixture
def browser_worker(browser_host, tmp_path):
    from distributedexec.agent import write_json
    from distributedexec.credentials import save
    from distributedexec.paths import config_dir
    from distributedexec.runtime import check_runtime, DockerExecutor
    from distributedexec.store import digest

    runtime = check_runtime()
    if runtime['state'] != 'ready':
        pytest.skip('Prepare a local Linux Docker runtime before browser execution tests')
    browser, url, token = browser_host
    headers = {'Authorization': 'Bearer ' + token}
    pairing = requests.post(url + '/api/desktop/pairing', headers=headers, timeout=3)
    pairing.raise_for_status()
    response = requests.post(url + '/api/pair', json={'code': pairing.json()['code'], 'name': 'Browser verification',
        'cpu': 2, 'memory_mb': 512, 'concurrency': 2}, timeout=3)
    response.raise_for_status()
    worker = response.json()
    reference = 'browser-test:' + worker['worker_id']
    save(reference, worker['credential'])
    service = tmp_path / 'browser-worker'
    service.mkdir()
    profile = service / 'profile.json'
    write_json(profile, {'host': url, 'worker_id': worker['worker_id'], 'credential_ref': reference,
        'endpoint': runtime['endpoint'], 'service_dir': str(service), 'concurrency': 2})
    output = open(service / 'worker.log', 'w', encoding='utf-8')
    process = subprocess.Popen([sys.executable, 'worker.py', '--config', str(profile), '--owner-pid', str(os.getpid())],
        stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = requests.get(url + '/api/status', headers=headers, timeout=3).json()
            if any(value['id'] == worker['worker_id'] and value['online'] and value['runtime_id'] for value in state['workers']):
                break
            assert process.poll() is None, (service / 'worker.log').read_text(encoding='utf-8')
            time.sleep(.1)
        else:
            pytest.fail('Browser test worker did not become ready')
        yield browser, url, token
    finally:
        write_json(service / 'command.json', {'command': 'stop'})
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate(); process.wait(timeout=10)
        output.close()
        DockerExecutor(runtime['endpoint'], worker['worker_id']).reconcile()
        requests.post(url + '/api/workers/' + worker['worker_id'] + '/revoke', headers=headers, timeout=3).raise_for_status()
        try:
            import keyring
            keyring.delete_password('DistributedExec', reference)
        except Exception:
            pass
        (config_dir() / (digest(reference) + '.secret')).unlink(missing_ok=True)


@pytest.mark.docker
def test_real_worker_executes_browser_job_logs_and_ordered_download(browser_worker):
    browser, url, token = browser_worker
    context, page, external = open_authenticated(browser, url, token, fallback=True)
    try:
        page.locator('#code').fill('def task(data):\n    import time\n    print("Browser verified chunk", data, flush=True)\n    if data[0] == 1: time.sleep(2)\n    return [item * 2 for item in data]')
        page.locator('#dataset').fill('[1, 2, 3, 4]')
        page.locator('#advancedSettings summary').click()
        page.locator('#chunkSize').fill('2')
        page.locator('#runBtn').click()
        page.wait_for_selector('#download', state='visible', timeout=60000)
        assert 'succeeded' in page.locator('#jobTitle').inner_text()
        assert '2/2 chunks completed' in page.locator('#progressText').inner_text()
        page.wait_for_function("document.getElementById('logs').textContent.includes('Browser verified chunk')")
        with page.expect_download() as event:
            page.locator('#download').click()
        assert json.loads(Path(event.value.path()).read_text(encoding='utf-8')) == [2, 4, 6, 8]
        assert external == []
    finally:
        context.close()
