"""Sandbox test suite.

The escape-attempt tests in this package are the load-bearing contract
of the ``agent-execution-sandbox`` change. Every test here exercises a
SPECIFIC known-bad pattern that a malicious agent might try:

* CPython subclasses-walking → subprocess.Popen
* Direct ``import ctypes``
* ``socket.connect`` to a non-MCP host
* CPU-time and wall-clock exhaustion
* Memory exhaustion (RLIMIT_AS)
* File writes (RLIMIT_FSIZE=0)
* Parent FD inheritance (must be blocked by spawn, not fork)

If a new escape vector is found, ADD a regression test here, do NOT
patch around it silently.
"""
