#!/bin/bash
# Start Gunicorn with production configuration
# Uses gunicorn.conf.py for worker count, timeouts, etc.
gunicorn -c gunicorn.conf.py app:app
