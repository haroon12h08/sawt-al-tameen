"""Local execution mode: a fully offline voice/text channel onto the same application layer.

This package is an *adapter*, exactly like ``agent_tools/elevenlabs.py`` is an adapter. It owns speech
recognition, speech synthesis, a local tool-calling model, conversation state and a browser UI. It owns no
insurance logic whatsoever: every fact it speaks comes back from the same three agent tools, the same rules
engine and the same recommendation engine that the hosted ElevenLabs agent calls.
"""
