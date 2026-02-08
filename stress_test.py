"""
Concurrent stress test for the Xiangqi server.

Simulates multiple users making AI move requests simultaneously.
Tests both throughput and latency under load.
"""

import requests
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

SERVER = 'http://8.137.86.5'

INITIAL_BOARD = [
    ['b_che','b_ma','b_xiang','b_shi','b_jiang','b_shi','b_xiang','b_ma','b_che'],
    [None]*9,
    [None,'b_pao',None,None,None,None,None,'b_pao',None],
    ['b_zu',None,'b_zu',None,'b_zu',None,'b_zu',None,'b_zu'],
    [None]*9, [None]*9,
    ['r_bing',None,'r_bing',None,'r_bing',None,'r_bing',None,'r_bing'],
    [None,'r_pao',None,None,None,None,None,'r_pao',None],
    [None]*9,
    ['r_che','r_ma','r_xiang','r_shi','r_shuai','r_shi','r_xiang','r_ma','r_che'],
]


def single_request(user_id):
    """Simulate one user requesting an AI move."""
    t0 = time.time()
    try:
        resp = requests.post(
            f'{SERVER}/ai-move',
            json={'board': INITIAL_BOARD},
            timeout=15
        )
        dt = time.time() - t0
        data = resp.json()

        if resp.status_code == 200 and data.get('status') == 'ok':
            return {'user': user_id, 'time': dt, 'status': 'ok',
                    'server_time': data.get('time', 0)}
        elif resp.status_code == 429:
            return {'user': user_id, 'time': dt, 'status': 'rate_limited'}
        else:
            return {'user': user_id, 'time': dt, 'status': 'error',
                    'detail': data.get('message', '')}
    except Exception as e:
        dt = time.time() - t0
        return {'user': user_id, 'time': dt, 'status': 'exception',
                'detail': str(e)}


def run_test(concurrent_users, label=""):
    """Run a batch of concurrent requests."""
    print(f"\n{'='*60}")
    print(f"  Test: {concurrent_users} concurrent AI requests {label}")
    print(f"{'='*60}")

    results = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=concurrent_users) as pool:
        futures = {pool.submit(single_request, i): i
                   for i in range(concurrent_users)}
        for future in as_completed(futures):
            results.append(future.result())

    total_time = time.time() - t0

    # Analyze results
    ok = [r for r in results if r['status'] == 'ok']
    limited = [r for r in results if r['status'] == 'rate_limited']
    errors = [r for r in results if r['status'] in ('error', 'exception')]

    if ok:
        latencies = [r['time'] for r in ok]
        avg_lat = sum(latencies) / len(latencies)
        max_lat = max(latencies)
        min_lat = min(latencies)
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]

        print(f"  Successful:    {len(ok)}/{concurrent_users}")
        print(f"  Rate limited:  {len(limited)}")
        print(f"  Errors:        {len(errors)}")
        print(f"  Total time:    {total_time:.2f}s")
        print(f"  Throughput:    {len(ok)/total_time:.1f} req/s")
        print(f"  Latency avg:   {avg_lat:.3f}s")
        print(f"  Latency min:   {min_lat:.3f}s")
        print(f"  Latency max:   {max_lat:.3f}s")
        print(f"  Latency P95:   {p95:.3f}s")
    else:
        print(f"  No successful requests!")
        print(f"  Rate limited:  {len(limited)}")
        print(f"  Errors:        {len(errors)}")

    for r in errors:
        print(f"  [Error] User {r['user']}: {r.get('detail', 'unknown')}")

    return ok, limited, errors


def main():
    print(f"Xiangqi Server Stress Test")
    print(f"Target: {SERVER}")
    print()

    # Check server is alive
    try:
        r = requests.get(f'{SERVER}/health', timeout=5)
        print(f"Server health: {r.json()}")
    except Exception as e:
        print(f"Server unreachable: {e}")
        sys.exit(1)

    # Test 1: Low concurrency (baseline)
    run_test(5, "(baseline)")

    # Test 2: Medium concurrency
    run_test(20, "(medium load)")

    # Test 3: High concurrency
    run_test(50, "(high load)")

    # Test 4: Burst test
    run_test(100, "(burst)")

    # Test 5: Sustained load (rapid fire)
    print(f"\n{'='*60}")
    print(f"  Test: Sustained load - 100 requests over 10 seconds")
    print(f"{'='*60}")

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = []
        for i in range(100):
            futures.append(pool.submit(single_request, i))
            time.sleep(0.1)  # 10 req/s sustained
        for f in as_completed(futures):
            results.append(f.result())

    total_time = time.time() - t0
    ok = [r for r in results if r['status'] == 'ok']
    limited = [r for r in results if r['status'] == 'rate_limited']
    latencies = [r['time'] for r in ok] if ok else [0]

    print(f"  Duration:      {total_time:.1f}s")
    print(f"  Successful:    {len(ok)}/100")
    print(f"  Rate limited:  {len(limited)}")
    print(f"  Avg latency:   {sum(latencies)/len(latencies):.3f}s")
    print(f"  Max latency:   {max(latencies):.3f}s")

    # Final stats
    print(f"\n{'='*60}")
    try:
        r = requests.get(f'{SERVER}/stats', timeout=5)
        stats = r.json()
        print(f"  Server stats: {stats}")
    except:
        pass
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
