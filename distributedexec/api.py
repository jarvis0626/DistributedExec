import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from filelock import FileLock
from .models import RunRequest, PairRequest, Heartbeat, Completion, LogRequest
from .paths import assets
from .store import Store, Conflict, digest, encode, one, rows, run, MAX_UPLOAD


def create_app(workspace, admin_token, allowed_hosts=None, shutdown=None):
    store = Store(workspace)
    hosts = set(allowed_hosts or ['localhost', '127.0.0.1', '[::1]'])

    @asynccontextmanager
    async def lifespan(app):
        async def reaper():
            while True:
                await asyncio.to_thread(store.reap)
                await asyncio.sleep(2)
        task = asyncio.create_task(reaper())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            store.engine.dispose()

    app = FastAPI(title='DistributedExec', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(PermissionError)
    async def denied(_, exc):
        return JSONResponse({'detail': str(exc)}, status_code=401)

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({'detail': 'Not found'}, status_code=404)

    @app.middleware('http')
    async def protect(request, next_call):
        host = request.headers.get('host', '')
        try:
            parsed_host = urlsplit('http://' + host).hostname
        except ValueError:
            parsed_host = None
        if not parsed_host or parsed_host not in hosts:
            return JSONResponse({'detail': 'Unapproved Host header'}, status_code=400)
        origin = request.headers.get('origin')
        if origin and origin != f'{request.url.scheme}://{host}':
            return JSONResponse({'detail': 'Origin mismatch'}, status_code=403)
        if request.method in ('POST', 'PUT', 'PATCH'):
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > store.limits.upload_bytes:
                    return JSONResponse({'detail': 'Request exceeds configured upload limit'}, status_code=413)
            request._body = bytes(body)
        response = await next_call(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; font-src 'self' data:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    def bearer(request):
        auth = request.headers.get('authorization', '')
        return auth[7:] if auth.startswith('Bearer ') else ''

    def admin(request: Request):
        token = bearer(request)
        if token and secrets.compare_digest(token, admin_token):
            return
        cookie = request.cookies.get('distributedexec_session', '')
        with store.read() as conn:
            session = one(conn, 'SELECT * FROM sessions WHERE token_hash=:h AND expires>:t', h=digest(cookie), t=store.clock())
        if not session:
            raise HTTPException(401, 'Open the dashboard from the desktop launcher to sign in')
        if request.method not in ('GET', 'HEAD'):
            csrf = request.headers.get('x-csrf-token', '')
            if not secrets.compare_digest(session['csrf_hash'], digest(csrf)) or not request.headers.get('origin'):
                raise HTTPException(403, 'CSRF token and Origin required')

    def worker(request: Request):
        token = bearer(request)
        with store.read() as conn:
            store.worker(conn, token)
        return token

    def local_admin(request: Request):
        if request.client.host not in ('127.0.0.1', '::1', 'localhost', 'testclient'):
            raise HTTPException(403, 'Desktop controls require a local connection')
        if not secrets.compare_digest(bearer(request), admin_token):
            raise HTTPException(401, 'Desktop authentication required')

    @app.get('/health')
    def health():
        return {'application': 'DistributedExec', 'ready': True}

    @app.get('/')
    def dashboard():
        # Preserve the user's existing explicit UTF-8 decoding fix.
        return HTMLResponse((assets() / 'static' / 'index.html').read_text(encoding='utf-8'))

    @app.post('/api/desktop/ticket', dependencies=[Depends(local_admin)])
    def ticket():
        value = secrets.token_urlsafe(32)
        with store.tx() as conn:
            run(conn, 'INSERT INTO tickets VALUES (:h,:e)', h=digest(value), e=store.clock() + 60)
        return {'ticket': value}

    class Bootstrap(BaseModel):
        ticket: str = Field(max_length=128)

    @app.post('/api/session')
    def session(req: Bootstrap, request: Request):
        if request.client.host not in ('127.0.0.1', '::1', 'localhost', 'testclient'):
            raise HTTPException(403, 'Bootstrap requires a local browser')
        with store.tx() as conn:
            ticket = one(conn, 'SELECT * FROM tickets WHERE token_hash=:h AND expires>:t', h=digest(req.ticket), t=store.clock())
            if not ticket:
                raise HTTPException(401, 'Bootstrap ticket expired or already used')
            run(conn, 'DELETE FROM tickets WHERE token_hash=:h', h=digest(req.ticket))
            value, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            run(conn, 'INSERT INTO sessions VALUES (:h,:c,:e)', h=digest(value), c=digest(csrf), e=store.clock() + 12 * 3600)
        response = JSONResponse({'csrf': csrf})
        response.set_cookie('distributedexec_session', value, httponly=True, samesite='strict', max_age=12 * 3600)
        response.set_cookie('distributedexec_csrf', csrf, samesite='strict', max_age=12 * 3600)
        return response

    @app.post('/api/pair')
    def pair(req: PairRequest, request: Request):
        return store.pair(req, request.client.host)

    @app.post('/api/desktop/pairing', dependencies=[Depends(local_admin)])
    def rotate_pairing():
        return store.rotate_pairing()

    @app.post('/api/desktop/shutdown', dependencies=[Depends(local_admin)])
    def stop_host():
        if shutdown:
            shutdown()
        return {'stopping': True}

    @app.post('/api/run', dependencies=[Depends(admin)])
    def submit(req: RunRequest, idempotency_key: str | None = Header(default=None)):
        if idempotency_key and len(idempotency_key) > 128:
            raise HTTPException(422, 'Idempotency key exceeds 128 characters')
        job = store.submit(req, idempotency_key)
        return {'job_id': job, 'num_chunks': store.job(job)['total_chunks']}

    @app.get('/api/status', dependencies=[Depends(admin)])
    def status():
        return store.status()

    @app.get('/api/jobs/{job}', dependencies=[Depends(admin)])
    def job_detail(job: str):
        return store.job(job)

    @app.post('/api/jobs/{job}/cancel', dependencies=[Depends(admin)])
    def cancel(job: str):
        store.cancel(job)
        return {'status': store.job(job)['status']}

    @app.post('/api/jobs/{job}/retry', dependencies=[Depends(admin)])
    def retry(job: str, idempotency_key: str = Header()):
        with store.read() as conn:
            old = one(conn, 'SELECT * FROM jobs WHERE id=:j', j=job)
        if not old:
            raise KeyError(job)
        if old['status'] not in ('failed', 'cancelled'):
            raise Conflict('Only failed or cancelled jobs can be retried')
        if len(idempotency_key) > 128:
            raise HTTPException(422, 'Idempotency key exceeds 128 characters')
        new = store.submit(RunRequest.model_validate_json(old['payload']), f'retry:{job}:{idempotency_key}', parent=job)
        return {'job_id': new}

    @app.get('/api/jobs/{job}/result', dependencies=[Depends(admin)])
    def result(job: str):
        payload = encode(store.result(job))
        # IDs are taken from a validated database record, never user filesystem paths.
        root = store.workspace / 'artifacts'
        root.mkdir(exist_ok=True)
        path = root / (job + '.json')
        if root.is_symlink() or path.is_symlink() or path.resolve().parent != root.resolve():
            raise HTTPException(400, 'Invalid artifact path')
        with FileLock(root / (job + '.lock'), timeout=5):
            # Completed output is immutable. Reusing the file permits concurrent Windows downloads.
            if path.exists() and (path.stat().st_size != len(payload) or digest(path.read_text(encoding='utf-8')) != digest(payload)):
                raise Conflict('Stored artifact is damaged; remove this artifact file while the host is stopped to regenerate it from accepted chunk results')
            if not path.exists():
                staging = root / (secrets.token_hex(16) + '.tmp')
                try:
                    with open(staging, 'x', encoding='utf-8') as f:
                        f.write(payload)
                    staging.replace(path)
                finally:
                    staging.unlink(missing_ok=True)
        with store.tx() as conn:
            run(conn, 'INSERT OR REPLACE INTO artifacts VALUES (:id,:j,:p,:s,:h)',
                id=job, j=job, p=path.name, s=len(payload), h=digest(payload))
        return FileResponse(path, media_type='application/json', filename=f'DistributedExec-{job}.json')

    @app.get('/api/jobs/{job}/preview', dependencies=[Depends(admin)])
    def preview(job: str):
        value = store.result(job)
        return {'items': value[:10], 'total_items': len(value)}

    @app.get('/api/jobs/{job}/logs', dependencies=[Depends(admin)])
    def logs(job: str, after: int = 0):
        store.job(job)
        with store.read() as conn:
            return rows(conn, '''SELECT l.rowid AS cursor,l.attempt_id,l.seq,l.stream,l.text,l.created FROM logs l
                JOIN attempts a ON a.id=l.attempt_id JOIN chunks c ON c.id=a.chunk_id
                WHERE c.job_id=:j AND l.rowid>:after ORDER BY l.rowid LIMIT 200''', j=job, after=max(0, after))

    @app.post('/api/workers/{worker_id}/revoke', dependencies=[Depends(admin)])
    def revoke(worker_id: str):
        with store.tx() as conn:
            if not run(conn, 'UPDATE workers SET revoked=1 WHERE id=:id', id=worker_id).rowcount:
                raise KeyError(worker_id)
        store.reap()
        return {'revoked': True}

    @app.post('/api/worker/heartbeat')
    def heartbeat(req: Heartbeat, credential=Depends(worker)):
        return store.heartbeat(credential, req)

    @app.post('/api/worker/claim')
    def claim(credential=Depends(worker)):
        return store.claim(credential) or {'status': 'no_tasks'}

    @app.post('/api/worker/attempts/{attempt}/complete')
    def complete(attempt: str, req: Completion, x_attempt_token: str = Header(), credential=Depends(worker)):
        return store.complete(credential, attempt, x_attempt_token, req)

    @app.post('/api/worker/attempts/{attempt}/logs')
    def append_logs(attempt: str, req: LogRequest, x_attempt_token: str = Header(), credential=Depends(worker)):
        store.append_log(credential, attempt, x_attempt_token, req)
        return {'accepted': True}

    app.mount('/static', StaticFiles(directory=assets() / 'static'), name='static')
    return app
