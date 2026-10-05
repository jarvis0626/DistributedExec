"""Prototype contract, replaced with durable regression tests in milestone B."""
import asyncio
import unittest
from types import SimpleNamespace
import host


class Baseline(unittest.TestCase):
    def setUp(self):
        host.jobs.clear()
        host.workers.clear()
        host.pending_chunks.clear()

    def test_wait_without_workers(self):
        result = asyncio.run(host.run_job(host.RunRequest(code='def task(data): return data', dataset=[1, 2])))
        self.assertEqual(result['num_chunks'], 1)
        self.assertEqual(host.pending_chunks[0]['data'], [1, 2])

    def test_ordered_aggregation(self):
        import time
        host.workers.update({str(i): {'last_seen': time.time(), 'ip': '127.0.0.1'} for i in range(2)})
        job = asyncio.run(host.run_job(host.RunRequest(code='def task(data): return data', dataset=[1, 2, 3, 4])))
        for index, result in [(1, [3, 4]), (0, [1, 2])]:
            asyncio.run(host.submit_result(host.WorkerResult(job_id=job['job_id'], chunk_id=index,
                worker_id='0', result=result, execution_time=0), SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'))))
        self.assertEqual(host.jobs[job['job_id']]['final_result'], [1, 2, 3, 4])

    @unittest.expectedFailure
    def test_empty_input(self):
        asyncio.run(host.run_job(host.RunRequest(code='def task(data): return data', dataset=[])))


if __name__ == '__main__':
    unittest.main()
