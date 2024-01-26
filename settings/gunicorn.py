#!/usr/bin/env python
""" Gunicorn configuration options
Includes a monitor that will restart the server
on file changes
"""
import multiprocessing

workers = multiprocessing.cpu_count() * 2 + 1
max_requests = 1000
worker_class = "gevent"
loglevel = "debug"
pidfile = "gunicorn.pid"
reload = True
