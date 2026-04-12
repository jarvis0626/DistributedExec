import asyncio
import time
import uuid
import math
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

app = FastAPI(title="Distributed Python Execution Host")

# Setup static files for serving UI
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Models
class RunRequest(BaseModel):
    code: str
    dataset: List[Any]
    function_name: str = "task"

class WorkerResult(BaseModel):
    job_id: str
    chunk_id: int
    worker_id: str
    result: Optional[List[Any]] = None
    error: Optional[str] = None
    execution_time: float

class WorkerRegister(BaseModel):
    worker_id: str

# State
workers: Dict[str, Dict[str, Any]] = {}  # worker_id -> {last_seen: float, ip: str}
WORKER_TIMEOUT = 30.0 # seconds

jobs = {} # job_id -> Job Info
pending_chunks = [] # List of dicts: {'job_id': str, 'chunk_id': int, 'code': str, 'data': List, 'function_name': str}


@app.get("/")
async def get_ui():
    with open(os.path.join(STATIC_DIR, "index.html"), "r") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/download/worker.py")
async def download_worker(request: Request):
    host_addr = request.headers.get("host", "localhost:8000")
    protocol = "https" if request.url.scheme == "https" else "http"
    full_url = f"{protocol}://{host_addr}"
    
    worker_path = os.path.join(os.path.dirname(__file__), "worker.py")
    with open(worker_path, "r") as f:
        content = f.read()
        
    # Inject the URL
    content = content.replace("INJECTED_HOST = None", f'INJECTED_HOST = "{full_url}"')
    
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": 'attachment; filename="worker.py"'}
    )



@app.post("/api/worker/register")
async def register_worker(req: WorkerRegister, request: Request):
    workers[req.worker_id] = {"last_seen": time.time(), "ip": request.client.host}
    return {"status": "ok"}


@app.get("/api/worker/poll")
async def poll_task(worker_id: str, request: Request):
    # Update heartbeat
    if worker_id in workers:
        workers[worker_id]["last_seen"] = time.time()
        workers[worker_id]["ip"] = request.client.host
    else:
        workers[worker_id] = {"last_seen": time.time(), "ip": request.client.host}
    
    if pending_chunks:
        chunk = pending_chunks.pop(0)
        # Mark chunk as processing
        job = jobs[chunk['job_id']]
        job['chunks'][chunk['chunk_id']]['status'] = 'processing'
        job['chunks'][chunk['chunk_id']]['worker'] = worker_id
        job['chunks'][chunk['chunk_id']]['start_time'] = time.time()
        return chunk
    
    return {"status": "no_tasks"}


@app.post("/api/worker/submit")
async def submit_result(res: WorkerResult, request: Request):
    if res.worker_id in workers:
        workers[res.worker_id]["last_seen"] = time.time()
        workers[res.worker_id]["ip"] = request.client.host
    else:
        workers[res.worker_id] = {"last_seen": time.time(), "ip": request.client.host}
    
    if res.job_id in jobs:
        job = jobs[res.job_id]
        chunk_info = job['chunks'].get(res.chunk_id)
        if chunk_info:
            if res.error:
                chunk_info['status'] = 'error'
                chunk_info['error'] = res.error
                job['status'] = 'error'
            else:
                chunk_info['status'] = 'completed'
                chunk_info['result'] = res.result
                job['completed_chunks'] += 1
                
                # Check if job is fully complete
                if job['completed_chunks'] == job['total_chunks']:
                    job['status'] = 'completed'
                    job['end_time'] = time.time()
                    
                    # Aggregate results based on chunk order
                    final_result = []
                    for i in range(job['total_chunks']):
                        final_result.extend(job['chunks'][i]['result'])
                    job['final_result'] = final_result
                    
    return {"status": "ok"}


@app.post("/api/run")
async def run_job(req: RunRequest):
    # Determine number of chunks based on active workers (or default if 0)
    now = time.time()
    active_workers = [w for w, d in workers.items() if now - d["last_seen"] < WORKER_TIMEOUT]
    num_workers = max(1, len(active_workers))
    
    # Simple chunking strategy
    dataset = req.dataset
    total_items = len(dataset)
    chunk_size = math.ceil(total_items / num_workers) if num_workers > 0 else total_items
    
    job_id = str(uuid.uuid4())
    job = {
        'id': job_id,
        'code': req.code,
        'function_name': req.function_name,
        'status': 'running',
        'total_chunks': 0,
        'completed_chunks': 0,
        'chunks': {},
        'start_time': time.time(),
        'end_time': None,
        'final_result': None
    }
    
    for i in range(0, total_items, chunk_size):
        chunk_data = dataset[i:i+chunk_size]
        chunk_id = len(job['chunks'])
        
        job['chunks'][chunk_id] = {
            'status': 'pending', # pending -> processing -> completed/error
            'result': None,
            'worker': None
        }
        
        pending_chunks.append({
            'job_id': job_id,
            'chunk_id': chunk_id,
            'code': req.code,
            'function_name': req.function_name,
            'data': chunk_data
        })
        
    job['total_chunks'] = len(job['chunks'])
    jobs[job_id] = job
    
    return {"job_id": job_id, "num_chunks": job['total_chunks']}


@app.get("/api/status")
async def get_status(job_id: Optional[str] = None):
    now = time.time()
    # clean stales
    active_workers = {w: d for w, d in workers.items() if now - d["last_seen"] < WORKER_TIMEOUT}
    workers.clear()
    workers.update(active_workers)
    
    response = {
        "active_workers": len(workers),
        "workers_list": [{"id": w, "ip": d["ip"]} for w, d in workers.items()]
    }
    
    if job_id and job_id in jobs:
        job = jobs[job_id]
        response["job"] = {
            "id": job['id'],
            "status": job['status'],
            "progress": f"{job['completed_chunks']}/{job['total_chunks']}",
            "start_time": job['start_time'],
            "end_time": job['end_time'],
            "final_result_preview": job['final_result'][:10] if job['final_result'] else None, # only preview
            "total_result_count": len(job['final_result']) if job['final_result'] else 0
        }
        
    return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
