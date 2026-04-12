import time
import requests
import json

HOST_URL = "http://localhost:8000"

def is_prime(n):
    if n <= 1: return False
    if n <= 3: return True
    if n % 2 == 0 or n % 3 == 0: return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True

# This is the code that will be executed remotely
CODE_PAYLOAD = """
def process_data(numbers):
    import math
    def is_prime(n):
        if n <= 1: return False
        if n <= 3: return True
        if n % 2 == 0 or n % 3 == 0: return False
        i = 5
        while i * i <= n:
            if n % i == 0 or n % (i + 2) == 0:
                return False
            i += 6
        return True
        
    results = []
    for x in numbers:
        # A heavy computation step to exaggerate processing time
        _ = math.factorial(min(x, 1500))  
        results.append({"number": x, "is_prime": is_prime(x)})
    return results
"""

DATASET = list(range(20000, 20500)) # 500 items

def run_local():
    print(f"\n[*] Running purely local benchmark for {len(DATASET)} items...")
    start_time = time.time()
    
    import math
    results = []
    for x in DATASET:
        # Replicate heavy computation load
        _ = math.factorial(min(x, 1500))
        results.append({"number": x, "is_prime": is_prime(x)})
    
    duration = time.time() - start_time
    print(f"[+] Local execution took: {duration:.3f} seconds\n")
    return duration

def run_distributed():
    print(f"[*] Submitting distributed job to {HOST_URL}...")
    
    payload = {
        "code": CODE_PAYLOAD,
        "dataset": DATASET,
        "function_name": "process_data"
    }
    
    try:
        res = requests.post(f"{HOST_URL}/api/run", json=payload)
        res.raise_for_status()
        job_id = res.json()['job_id']
    except Exception as e:
        print(f"[-] Failed to submit job: {e}")
        return None
        
    print(f"[*] Job ID: {job_id}. Polling for completion...")
    
    start_time = time.time()
    
    while True:
        status_res = requests.get(f"{HOST_URL}/api/status?job_id={job_id}")
        data = status_res.json()
        
        job_info = data.get('job')
        if not job_info:
            print("[-] Job disappeared from host.")
            return None
            
        status = job_info['status']
        print(f"    Progress: {job_info['progress']} | Status: {status}")
        
        if status in ['completed', 'error']:
            break
            
        time.sleep(0.5)
        
    duration = time.time() - start_time
    
    if status == 'completed':
        print(f"\n[+] Distributed execution finished in: {duration:.3f} seconds\n")
        return duration
    else:
        print("[-] Distributed job ended with error.")
        return None

if __name__ == "__main__":
    print("================== BENCHMARK ==================")
    print("Ensure host is running. Make sure at least 1 (preferably 2+) workers are connected.")
    print("Checking host...")
    try:
        status = requests.get(f"{HOST_URL}/api/status").json()
        active_workers = status.get('active_workers', 0)
        print(f"Active workers detected: {active_workers}")
        if active_workers == 0:
            print("WARNING: No workers are connected. The distributed job will wait indefinitely until a worker connects.")
    except Exception:
        print("ERROR: Host is not reachable.")
        exit(1)
        
    time_local = run_local()
    time_dist = run_distributed()
    
    if time_dist:
        print("================== SUMMARY ==================")
        print(f"Local Execution:       {time_local:.3f}s")
        print(f"Distributed Execution: {time_dist:.3f}s (with {active_workers} worker(s))")
        if time_local > time_dist:
            speedup = time_local / time_dist
            print(f"Speedup:               {speedup:.2f}x faster!")
        else:
            overhead = time_dist / time_local
            print(f"Slower due to overhead or not enough workers ({overhead:.2f}x) - Try with larger dataset or more workers.")
