from pathlib import Path
import shutil
import uuid

import pytest


@pytest.fixture
def tmp_path() -> Path:
    # Keep pytest temp dirs inside the repo to avoid Windows sandbox permission issues.
    base = Path(__file__).resolve().parents[1] / '.codex_test_tmp'
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
