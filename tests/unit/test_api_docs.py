import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_generated_api_docs_are_current():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_api_docs.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
