from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402


def serialized_schema() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the generated OpenAPI schema.")
    parser.add_argument("--check", action="store_true", help="Fail if docs/openapi.json is out of date.")
    args = parser.parse_args()
    destination = Path("docs/openapi.json")
    content = serialized_schema()
    if args.check:
        if not destination.exists() or destination.read_text(encoding="utf-8") != content:
            print("docs/openapi.json is missing or out of date; run python scripts/export_openapi.py")
            return 1
        return 0
    destination.write_text(content, encoding="utf-8")
    print(f"Wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
