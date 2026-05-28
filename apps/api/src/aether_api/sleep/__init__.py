"""Sleep Phase — reflection-and-learning orchestrator.

Public surface:

* :func:`orchestrator.run_sleep_phase` — async workflow entrypoint
  (called by the scheduler, the manual-trigger endpoint, and the
  Crítico in-process listener).
* :func:`classifier.classify_changes` — pure-Python risk classifier.
* :func:`applier.apply_snapshot` / :func:`applier.revert_to_parent` —
  persistence helpers.
* :func:`boot_sweep.recover_stale_runs` — lifespan hook.

The package imports the sandbox engine via
:func:`aether_api.sandbox.engine.Engine.run_agent` to execute each
agent's reflection entrypoint. No agent code runs outside that
boundary — that invariant is the reason the Sleep Phase change is
gated on the agent-execution-sandbox change being live.
"""
