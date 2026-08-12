from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    for document in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (document.parent / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                errors.append(f"{document.relative_to(ROOT)}: broken link {target}")

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    required_locked_values = {
        'NYAA_CATEGORY_ID: "3_1"',
        'DATA_HARD_LIMIT_BYTES: "350000000"',
        'CACHE_DB_TARGET_BYTES: "256000000"',
        'SQLITE_WAL_HARD_LIMIT_BYTES: "32000000"',
        'DEBUG_MAX_BYTES: "50000000"',
        '"49191:8000"',
    }
    for value in required_locked_values:
        if value not in compose:
            errors.append(f"docker-compose.yml: missing locked value {value}")

    if errors:
        print("\n".join(errors))
        return 1
    print("Documentation links and locked Compose invariants are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
