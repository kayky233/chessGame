"""
Gunicorn production configuration for Xiangqi server.

Tuned for a 2-core / 1.8GB RAM VPS serving CPU-bound AI workload.
"""

import multiprocessing

# ---- Workers ----
# Formula: 2 * CPU_CORES + 1 (classic for CPU-bound)
workers = 2 * multiprocessing.cpu_count() + 1

# sync workers give best CPU throughput for AI computation
# (each worker = separate process = real parallelism, no GIL)
worker_class = 'sync'

# ---- Networking ----
bind = '0.0.0.0:80'
keepalive = 5
backlog = 256          # Pending connection queue size

# ---- Timeouts ----
timeout = 120          # Allow slower AI turns without worker timeout
graceful_timeout = 30  # Grace period for in-flight requests on restart

# ---- Worker Recycling (prevents memory leaks) ----
max_requests = 2000
max_requests_jitter = 200  # Stagger restarts to avoid thundering herd

# ---- Logging ----
accesslog = '-'        # stdout
errorlog = '-'         # stderr
loglevel = 'info'

# ---- Preload (save memory via copy-on-write) ----
preload_app = True
