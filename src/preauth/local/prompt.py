"""The local agent's instructions.

Behaviour lives in the prompt; truth lives in the backend. The prompt never restates a benefit rule, a limit or
a document requirement — those come back from ``check_coverage_rule``.

Local mode uses its own prompt rather than the hosted agent's. That is a size decision, not a policy one: a 7B
model on a CPU spends most of a turn re-reading its prompt, and the hosted prompt is roughly three times longer
than this one. Everything that makes the agent safe is carried over word for word — the AI disclosure, the
opening line, verification before policy detail, the tool-first rule, the refusal to issue a decision and the
sentence it says when pressed for one. ``tests/unit/test_local_prompt.py`` fails if any of them goes missing.

Point ``PREAUTH_LOCAL_SYSTEM_PROMPT`` at a file to use a different one — the hosted prompt, for instance, on a
machine with a model large and fast enough to take it.
"""

import os
from functools import lru_cache
from pathlib import Path

VOICE_DIR = Path(__file__).resolve().parents[3] / "voice"
LOCAL_PROMPT_PATH = VOICE_DIR / "local_system_prompt.md"
HOSTED_PROMPT_PATH = VOICE_DIR / "system_prompt.md"

# Spoken before the model is involved at all: the opening line is fixed by the prompt, so there is nothing for a
# model to decide and no reason to make the caller wait for a generation.
GREETING = (
    "Sawt Assurance pre-authorisation line, this is an automated assistant. "
    "The call is recorded for audit. Who am I speaking with?"
)

# What the agent says instead of a decision, word for word from the hosted prompt.
DECISION_BOUNDARY = (
    "I\'m not able to issue a final decision, only prepare a recommendation for our team to confirm. "
    "That\'s a safeguard on every case, not specific to yours."
)


def prompt_path() -> Path:
    override = (os.environ.get("PREAUTH_LOCAL_SYSTEM_PROMPT") or "").strip()
    return Path(override) if override else LOCAL_PROMPT_PATH


@lru_cache(maxsize=4)
def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Agent prompt file is missing: {path}")
    return path.read_text().strip()


def system_prompt() -> str:
    return _read(prompt_path())
