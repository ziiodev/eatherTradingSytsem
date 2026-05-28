"""Wine subprocess drivers (``WineRunnerProtocol`` implementations).

Phase 3 will add the real driver. For now, only the :mod:`null` stub exists,
which lets the server boot without Wine and surfaces a clear error if any
caller actually tries to run a job.
"""
