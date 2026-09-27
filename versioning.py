"""Code, durable experiment, and individual backend-run identity helpers."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import uuid

from config import ROOT


def git_code_version(root=ROOT):
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'], cwd=Path(root),
            capture_output=True, text=True, timeout=2, check=True)
        value = result.stdout.strip()
        return value or 'unknown'
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def new_backend_run_id(now=None):
    now = now or datetime.now(timezone.utc)
    return f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def new_session_id(now=None):
    """Compatibility alias for older callers; new code stores this as a run ID."""
    return new_backend_run_id(now)
