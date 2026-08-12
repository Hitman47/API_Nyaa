from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture(scope="session")
def sample_rss() -> str:
    return (Path(__file__).parent / "fixtures" / "sample_rss.xml").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def sample_detail() -> str:
    return (Path(__file__).parent / "fixtures" / "sample_detail.html").read_text(encoding="utf-8")


@pytest.fixture
def workspace_tmp() -> Iterator[Path]:
    """Use an explicit workspace directory to avoid Windows pytest ACL issues."""

    root = Path.cwd() / "test-artifacts"
    root.mkdir(exist_ok=True)
    path = root / uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
