"""The local prompt is shorter than the hosted one. It must not be weaker.

Local mode uses its own prompt because a 7B model on a CPU spends most of a turn re-reading its prompt. That is a
size decision, so these tests check that nothing which makes the agent *safe* was lost in making it shorter, and
that neither prompt tries to restate the benefit rules the backend owns.
"""

import re

import pytest

from preauth.local.prompt import (
    DECISION_BOUNDARY,
    GREETING,
    HOSTED_PROMPT_PATH,
    LOCAL_PROMPT_PATH,
    prompt_path,
    system_prompt,
)


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


LOCAL = flat(LOCAL_PROMPT_PATH.read_text())
HOSTED = flat(HOSTED_PROMPT_PATH.read_text())

# Every one of these is a safety behaviour, not a stylistic preference. If a line is ever dropped from the local
# prompt to save tokens, this list is what stops it.
NON_NEGOTIABLES = [
    ("identifies itself as an AI", "automated assistant"),
    ("states that the call is recorded", "recorded for audit"),
    ("names the insurer", "sawt assurance"),
    ("verifies before discussing policy detail", "until it returns `authorised: true`"),
    ("refuses to issue a final decision", "never issue or imply a final approval or denial"),
    ("has the exact wording for refusing", flat(DECISION_BOUNDARY)),
    ("refuses to state cover without a source", "that did not come back from `check_coverage_rule`"),
    ("refuses to skip verification under pressure", "never skip verification"),
    ("treats a lapsed policy as a verification failure", "lapsed policy is a verification failure"),
    ("knows emergencies need no pre-authorisation", "emergency treatment never needs pre-authorisation"),
    ("turns patients away from a business line", "business callers"),
    ("offers a callback when it has no tool", "no tool for"),
]


@pytest.mark.parametrize("behaviour,phrase", NON_NEGOTIABLES, ids=[n for n, _ in NON_NEGOTIABLES])
def test_the_local_prompt_keeps_every_safety_behaviour(behaviour, phrase):
    assert flat(phrase) in LOCAL, behaviour


def test_the_greeting_the_code_speaks_is_the_one_the_prompt_specifies():
    """The opening line is said without calling the model, so the two must not drift apart."""
    assert flat(GREETING) in LOCAL


def test_the_local_prompt_is_materially_shorter_than_the_hosted_one():
    assert len(LOCAL) < len(HOSTED) * 0.7, "the point of a local prompt is that it fits a small model's context"


def test_the_local_prompt_names_the_same_three_tools_and_no_others():
    from preauth.agent_tools.toolbox import TOOLS

    for tool in TOOLS:
        assert tool.name in LOCAL
    for invented in ("approve_case", "record_decision", "finalise_authorisation", "deny_case"):
        assert invented not in LOCAL


def test_neither_prompt_restates_a_benefit_rule():
    """Behaviour belongs in the prompt; cover, limits and thresholds belong to the catalogue and the rules."""
    for name, prompt in (("local", LOCAL), ("hosted", HOSTED)):
        assert "aed 150,000" not in prompt and "aed 500,000" not in prompt, name
        assert not re.search(r"\bco-?payment is \d", prompt), name
        # No tier is described as covering or excluding anything; that is the schedule's job.
        assert "is not covered" not in prompt, name


def test_the_prompt_file_can_be_overridden(monkeypatch, tmp_path):
    """A machine with a larger model can be pointed at the hosted prompt, or any other."""
    assert prompt_path() == LOCAL_PROMPT_PATH
    monkeypatch.setenv("PREAUTH_LOCAL_SYSTEM_PROMPT", str(HOSTED_PROMPT_PATH))
    assert prompt_path() == HOSTED_PROMPT_PATH
    assert "IDENTITY" in system_prompt()

    monkeypatch.setenv("PREAUTH_LOCAL_SYSTEM_PROMPT", str(tmp_path / "absent.md"))
    with pytest.raises(FileNotFoundError, match="prompt file is missing"):
        system_prompt()
