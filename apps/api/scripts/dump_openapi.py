"""Dump the FastAPI OpenAPI schema to apps/api/openapi.json.

Used by Phase 5's type generation pipeline so we can run openapi-typescript
without booting the server. CI regenerates and checks for drift.
"""
import json
from pathlib import Path

from aether_api.main import app


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "openapi.json"
    out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
