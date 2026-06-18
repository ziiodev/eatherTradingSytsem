---
name: tests-target-test-db
description: All backend tests must run against the dedicated test database (not dev/prod). Sub-agents need to be told explicitly when launching apply/verify phases.
metadata:
  type: feedback
---

Backend tests in apps/api/ MUST run against the test database (typically `aether_test` via testcontainers + the `migrated_db` fixture chain in conftest.py). Never run tests against a dev or prod database.

**Why:** The user confirmed this convention during project-chat Phase 1+2 apply — tests must remain isolated from real project state, and migrations like `0014_chat` get tested via upgrade/downgrade round-trips that would destroy data on any non-test DB.

**How to apply:**
- When launching SDD apply/verify sub-agents, explicitly instruct them to run tests via `uv run pytest` against the existing testcontainers infrastructure — never pointing at `DATABASE_URL` directly unless it's the test DB.
- Migration smoke tests (`alembic upgrade head` + `downgrade -1`) MUST target a test DB only.
- If a sub-agent reports running pytest globally and getting pre-existing flakes (the `pytest-asyncio` event-loop / `migrated_db` cross-suite collision), confirm they were on the test DB — don't worry about prod impact.
- Per-suite scoped runs (`pytest tests/sandbox/`, `pytest tests/test_chat_*`, etc.) are preferred to avoid the cross-suite session fixture flake. The pre-existing flake itself is not a regression.
