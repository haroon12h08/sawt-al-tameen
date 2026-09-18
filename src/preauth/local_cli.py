"""Text-only local agent, for a terminal.

    uv run python -m preauth.local_cli

The same runtime the browser console uses, with the microphone and the loudspeaker switched off. This is the
deterministic way to exercise a complete pre-authorisation call: no audio devices, no browser, no HTTP — just
the local model driving the real tools against the real database.

    uv run python -m preauth.local_cli --script calls/lapsed_member.txt   # replay scripted caller turns
"""

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from preauth.application.services import build_services
from preauth.infrastructure.db.session import build_engine, build_session_factory
from preauth.infrastructure.observability import configure_logging
from preauth.infrastructure.settings import Settings
from preauth.local.config import LocalSettings, SttProvider, TtsProvider
from preauth.local.errors import LocalModeError
from preauth.local.runtime import LocalRuntime
from preauth.seed.catalogue import is_loaded

BANNER = "Sawt Assurance local agent — type 'quit' to end the call and store the transcript."


def _print_turn(who: str, text: str) -> None:
    print(f"\n{who:>7}: {text}")


def _print_tools(payload: dict) -> None:
    for call in payload.get("tool_calls", []):
        mark = "ok" if call["ok"] else f"failed ({call['error_code']})"
        print(f"   [tool] {call['tool']} -> {mark}")


def _print_state(state: dict) -> None:
    bits = [f"verified={'yes' if state['verified'] else 'no'}"]
    if state["case_reference"]:
        bits.append(f"case={state['case_reference']}")
        bits.append(f"status={state['case_status']}")
    if state["recommendation_outcome"]:
        bits.append(f"recommendation={state['recommendation_outcome']} (advisory)")
    if state["escalation_rule_ids"]:
        bits.append("escalation=" + ",".join(state["escalation_rule_ids"]))
    if state["missing_intake_fields"]:
        bits.append("still needed: " + ", ".join(state["missing_intake_fields"]))
    print(f"   [case] {'  '.join(bits)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--script", type=Path, help="file of caller turns, one per line, replayed in order")
    parser.add_argument("--log-level", default="WARNING", help="backend log level (default: WARNING)")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    configure_logging(args.log_level)
    logging.getLogger("preauth.local").setLevel(logging.INFO)

    session_factory = build_session_factory(build_engine(settings.database_url))
    with session_factory() as session:
        if not is_loaded(session):
            print(
                "The UAE catalogue is not loaded. Run:\n"
                "  uv run alembic upgrade head\n"
                "  uv run python -m preauth.seed --scenarios",
                file=sys.stderr,
            )
            return 2

    services = build_services(session_factory)
    # A terminal has no microphone and no loudspeaker; the speech engines are off by construction, not by a
    # runtime branch, so this path works on a machine where neither is installed.
    local = replace(
        LocalSettings.from_env(), stt_provider=SttProvider.DISABLED, tts_provider=TtsProvider.DISABLED
    )
    runtime = LocalRuntime.build(services, local)

    try:
        runtime.agent.model.check()
    except LocalModeError as e:
        print(f"{e.message}\n  fix: {e.details}", file=sys.stderr)
        return 3

    print(BANNER)
    print(f"model: {runtime.agent.model.name}")
    opening = runtime.start()
    conversation_id = opening["conversation_id"]
    print(f"conversation: {conversation_id}")
    _print_turn("agent", opening["reply"])

    lines = iter(args.script.read_text().splitlines()) if args.script else None
    while True:
        try:
            if lines is None:
                text = input("\n caller: ").strip()
            else:
                text = next(lines, "quit").strip()
                if text:
                    _print_turn("caller", text)
        except (EOFError, KeyboardInterrupt):
            text = "quit"
        if not text:
            continue
        if text.lower() in {"quit", "exit"}:
            break
        try:
            payload = runtime.say(conversation_id, text)
        except LocalModeError as e:
            print(f"   [error] {e.code}: {e.message}", file=sys.stderr)
            continue
        _print_tools(payload)
        if payload.get("decision_language_blocked"):
            print("   [guard] a final-decision statement was removed from the reply")
        _print_turn("agent", payload["reply"])
        _print_state(payload["state"])

    finished = runtime.finish(conversation_id)
    print(f"\nCall ended. Call record {finished['call_record_id']}.")
    if finished["linked_case_ids"]:
        print(
            f"{len(finished['linked_case_ids'])} case(s) now have their transcript on record and can be "
            "reviewed by a qualified human. Nothing was approved or denied on this call."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
