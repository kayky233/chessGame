"""
Flask API for Xiangqi (Chinese Chess).

Stateless design: no game state stored in memory between requests.
The frontend sends the full board state with every request.
Designed to run behind Gunicorn with multiple workers.

Concurrency features:
- Per-IP rate limiting to prevent abuse
- Active request tracking and monitoring
- /stats endpoint for live server metrics
"""

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from engine import XiangqiEngine
import time
import threading
import os

app = Flask(__name__)
CORS(app)


# ================================================================
# Rate Limiter (per-worker, token-bucket style)
# ================================================================
class RateLimiter:
    """Simple per-IP rate limiter using sliding window."""

    def __init__(self, max_requests=10, window_sec=10):
        self.max_requests = max_requests
        self.window = window_sec
        self.requests = {}  # ip -> [timestamps]
        self.lock = threading.Lock()

    def allow(self, ip):
        now = time.time()
        with self.lock:
            if ip not in self.requests:
                self.requests[ip] = []
            # Evict old timestamps
            self.requests[ip] = [t for t in self.requests[ip] if now - t < self.window]
            if len(self.requests[ip]) >= self.max_requests:
                return False
            self.requests[ip].append(now)
            return True

    def cleanup(self):
        """Remove stale IPs (call periodically)."""
        now = time.time()
        with self.lock:
            stale = [ip for ip, ts in self.requests.items()
                     if not ts or now - ts[-1] > self.window * 2]
            for ip in stale:
                del self.requests[ip]


# ================================================================
# Server Metrics (per-worker)
# ================================================================
class Metrics:
    """Track request counts and active requests for this worker."""

    def __init__(self):
        self.lock = threading.Lock()
        self.total_requests = 0
        self.total_ai_requests = 0
        self.active_ai_requests = 0
        self.total_ai_time = 0.0
        self.start_time = time.time()

    def ai_start(self):
        with self.lock:
            self.total_requests += 1
            self.total_ai_requests += 1
            self.active_ai_requests += 1

    def ai_end(self, duration):
        with self.lock:
            self.active_ai_requests -= 1
            self.total_ai_time += duration

    def page_hit(self):
        with self.lock:
            self.total_requests += 1

    def snapshot(self):
        with self.lock:
            uptime = time.time() - self.start_time
            avg_ai = (self.total_ai_time / self.total_ai_requests
                      if self.total_ai_requests > 0 else 0)
            return {
                'worker_pid': os.getpid(),
                'uptime_seconds': round(uptime),
                'total_requests': self.total_requests,
                'total_ai_requests': self.total_ai_requests,
                'active_ai_requests': self.active_ai_requests,
                'avg_ai_time': round(avg_ai, 3),
                'ai_throughput_per_min': round(
                    self.total_ai_requests / (uptime / 60), 1
                ) if uptime > 0 else 0,
            }


limiter = RateLimiter(max_requests=10, window_sec=10)
metrics = Metrics()


# ================================================================
# Routes
# ================================================================

@app.route('/')
def index():
    """Serve the frontend."""
    metrics.page_hit()
    return render_template('index.html')


@app.route('/ai-move', methods=['POST'])
def ai_move():
    """
    AI move endpoint.
    Input:  JSON { "board": [[...], ...] }  (10x9 matrix)
    Output: JSON { "status": "ok", "move": {"from": [r,c], "to": [r,c]}, "time": float }
    """
    # --- Rate limiting ---
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip:
        client_ip = client_ip.split(',')[0].strip()

    if not limiter.allow(client_ip):
        return jsonify({
            'status': 'error',
            'message': '请求过于频繁，请稍后再试 (rate limited)'
        }), 429

    # --- Validate input ---
    data = request.get_json(silent=True)
    if not data or 'board' not in data:
        return jsonify({
            'status': 'error',
            'message': 'Missing board data'
        }), 400

    board = data['board']

    if not isinstance(board, list) or len(board) != 10:
        return jsonify({
            'status': 'error',
            'message': 'Board must be a 10-row array'
        }), 400

    for i, row in enumerate(board):
        if not isinstance(row, list) or len(row) != 9:
            return jsonify({
                'status': 'error',
                'message': f'Row {i} must have 9 columns'
            }), 400

    # --- Run AI ---
    depth = data.get('depth', 4)
    depth = max(1, min(depth, 6))  # Clamp to safe range

    metrics.ai_start()
    t0 = time.time()
    try:
        engine = XiangqiEngine(depth=depth)
        move = engine.get_best_move(board)
    finally:
        dt = time.time() - t0
        metrics.ai_end(dt)

    if move is None:
        return jsonify({
            'status': 'ok',
            'move': None,
            'message': 'No valid moves for AI',
            'time': round(dt, 3)
        })

    fr, fc, tr, tc = move
    return jsonify({
        'status': 'ok',
        'move': {
            'from': [fr, fc],
            'to': [tr, tc]
        },
        'time': round(dt, 3),
        'nodes': engine.nodes
    })


@app.route('/stats')
def stats():
    """Server metrics endpoint for monitoring."""
    return jsonify(metrics.snapshot())


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'pid': os.getpid()})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
