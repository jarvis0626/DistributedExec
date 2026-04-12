import sys
import time
import uuid
import requests
import multiprocessing
import traceback

def safe_execute(code_str: str, function_name: str, data: list, result_queue: multiprocessing.Queue):
    """
    Executes the task in an isolated process to prevent main worker failure
    and enforce timeouts. Note: Real production python sandboxing requires
    more specialized tools (e.g. PyPy sandbox, Docker).
    """
    try:
        # Create a restricted globals dict
        safe_globals = {
            '__builtins__': {
                'str': str, 'int': int, 'float': float, 'list': list, 'dict': dict,
                'tuple': tuple, 'set': set, 'bool': bool, 'len': len, 'range': range,
                'min': min, 'max': max, 'sum': sum, 'abs': abs, 'round': round,
                'print': print, 'Exception': Exception,
                '__import__': __import__ # Allow basic imports, though risky!
            }
        }
        
        # Execute the code definition into safe_globals
        exec(code_str, safe_globals)
        
        if function_name not in safe_globals:
            raise ValueError(f"Function '{function_name}' not found in provided code.")
            
        task_func = safe_globals[function_name]
        
        # Call the target function
        result = task_func(data)
        
        result_queue.put({"status": "success", "result": result})
        
    except Exception as e:
        error_info = traceback.format_exc()
        result_queue.put({"status": "error", "error": f"{str(e)}\n\nTraceback:\n{error_info}"})


def worker_loop(host_url: str):
    worker_id = str(uuid.uuid4())
    print(f"[*] Starting worker {worker_id}")
    print(f"[*] Connecting to host: {host_url}")
    
    # Register
    try:
        requests.post(f"{host_url}/api/worker/register", json={"worker_id": worker_id}, timeout=5)
        print("[+] Successfully registered with host.")
    except Exception as e:
        print(f"[-] Could not connect to host. Make sure it's running. {e}")
        return

    while True:
        try:
            # Poll for task
            res = requests.get(f"{host_url}/api/worker/poll", params={"worker_id": worker_id}, timeout=10)
            data = res.json()
            
            if data.get("status") == "no_tasks":
                time.sleep(1) # wait before polling again
                continue
                
            # If we get here, it's a task chunk
            job_id = data['job_id']
            chunk_id = data['chunk_id']
            code = data['code']
            func_name = data['function_name']
            dataset = data['data']
            
            print(f"[*] Received task! Job: {job_id[:8]}... Chunk: {chunk_id} | Data items: {len(dataset)}")
            
            # Setup multiprocessing queue to communicate
            q = multiprocessing.Queue()
            
            # Start process
            p = multiprocessing.Process(target=safe_execute, args=(code, func_name, dataset, q))
            start_time = time.time()
            p.start()
            
            # Implement timeout
            TIMEOUT_SECONDS = 60
            p.join(TIMEOUT_SECONDS)
            
            exec_time = time.time() - start_time
            
            if p.is_alive():
                print(f"[-] Task execution timed out (> {TIMEOUT_SECONDS}s). Terminating process.")
                p.terminate()
                p.join()
                result_payload = {
                    "job_id": job_id, "chunk_id": chunk_id, "worker_id": worker_id,
                    "error": "Execution Timeout", "execution_time": exec_time
                }
            else:
                if not q.empty():
                    proc_res = q.get()
                    if proc_res['status'] == 'success':
                        print(f"[+] Task completed successfully in {exec_time:.3f}s")
                        result_payload = {
                            "job_id": job_id, "chunk_id": chunk_id, "worker_id": worker_id,
                            "result": proc_res['result'], "execution_time": exec_time
                        }
                    else:
                        print(f"[-] Task encountered an error during execution.")
                        result_payload = {
                            "job_id": job_id, "chunk_id": chunk_id, "worker_id": worker_id,
                            "error": proc_res['error'], "execution_time": exec_time
                        }
                else:
                    print("[-] Process exited without returning data.")
                    result_payload = {
                        "job_id": job_id, "chunk_id": chunk_id, "worker_id": worker_id,
                        "error": "Process crashed unexpectedly.", "execution_time": exec_time
                    }
                    
            # Submit result
            requests.post(f"{host_url}/api/worker/submit", json=result_payload)
            
        except requests.exceptions.RequestException as e:
            print(f"[-] Connection issue with host: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            print(f"[-] Worker error: {e}")
            time.sleep(2)


if __name__ == '__main__':
    INJECTED_HOST = None # Will be replaced by host during download
    host_url = INJECTED_HOST
    
    if len(sys.argv) >= 2:
        host_url = sys.argv[1].rstrip('/')
    elif host_url is None:
        print("Usage: python worker.py <host_url>")
        print("Example: python worker.py http://localhost:8000")
        sys.exit(1)
    
    # Must use spawn process for proper isolated task running especially on windows/macos
    multiprocessing.set_start_method('spawn')
    
    try:
        worker_loop(host_url)
    except KeyboardInterrupt:
        print("\n[*] Worker stopping...")
        sys.exit(0)
