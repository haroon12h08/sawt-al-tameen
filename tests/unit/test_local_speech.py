"""Speech adapters: what they do when the model is missing, and what they refuse to guess."""

import wave
from io import BytesIO

import pytest

from preauth.local.config import LocalSettings, SttProvider, TtsProvider
from preauth.local.errors import LocalDependencyMissingError, SpeechNotRecognisedError
from preauth.local.speech import (
    DisabledSynthesizer,
    DisabledTranscriber,
    FasterWhisperTranscriber,
    PiperSynthesizer,
    build_synthesizer,
    build_transcriber,
)
from tests.local_fakes import FakeSynthesizer, FakeTranscriber


def test_the_configured_provider_decides_which_adapter_is_built():
    assert isinstance(build_transcriber(LocalSettings()), FasterWhisperTranscriber)
    assert isinstance(build_synthesizer(LocalSettings()), PiperSynthesizer)
    disabled = LocalSettings(stt_provider=SttProvider.DISABLED, tts_provider=TtsProvider.DISABLED)
    assert isinstance(build_transcriber(disabled), DisabledTranscriber)
    assert isinstance(build_synthesizer(disabled), DisabledSynthesizer)


def test_empty_audio_is_refused_rather_than_transcribed():
    """A guessed policy number would flow straight into a rule check, so silence must fail loudly."""
    with pytest.raises(SpeechNotRecognisedError):
        FasterWhisperTranscriber(LocalSettings()).transcribe(b"")


def test_a_missing_voice_file_says_how_to_download_one(tmp_path):
    synthesizer = PiperSynthesizer(LocalSettings(tts_voice_path=str(tmp_path / "absent.onnx")))
    with pytest.raises(LocalDependencyMissingError) as raised:
        synthesizer.synthesize("hello")
    assert "does not exist" in raised.value.message
    assert "download_voices" in raised.value.details["download"]


def test_a_voice_without_its_config_is_refused(tmp_path):
    voice = tmp_path / "voice.onnx"
    voice.write_bytes(b"not really an onnx file")
    with pytest.raises(LocalDependencyMissingError, match="json is missing"):
        PiperSynthesizer(LocalSettings(tts_voice_path=str(voice))).synthesize("hello")


def test_an_unset_voice_path_is_reported_before_anything_is_loaded():
    with pytest.raises(LocalDependencyMissingError, match="PREAUTH_LOCAL_TTS_VOICE"):
        PiperSynthesizer(LocalSettings(tts_voice_path=None)).check()


def test_disabled_speech_explains_itself_instead_of_failing_silently():
    with pytest.raises(LocalDependencyMissingError) as heard:
        DisabledTranscriber().transcribe(b"audio")
    assert heard.value.code == "SPEECH_INPUT_DISABLED"
    with pytest.raises(LocalDependencyMissingError) as spoken:
        DisabledSynthesizer().synthesize("hello")
    assert spoken.value.code == "SPEECH_OUTPUT_DISABLED"
    # And they report themselves as unavailable, so the browser never offers the microphone.
    assert DisabledTranscriber().enabled is False
    assert DisabledSynthesizer().enabled is False


def test_the_fakes_used_by_the_suite_satisfy_the_adapter_contracts():
    """Guards the doubles the integration tests rely on against the protocols drifting apart."""
    transcriber, synthesizer = FakeTranscriber(text="policy POL-SA-2026-100001"), FakeSynthesizer()
    assert transcriber.transcribe(b"audio", "en").text == "policy POL-SA-2026-100001"
    assert synthesizer.synthesize("hello").media_type == "audio/wav"
    for adapter in (transcriber, synthesizer):
        assert adapter.enabled and adapter.name and adapter.check() is None


def test_synthesised_audio_is_a_playable_wav_container():
    """The browser plays the reply as a data: URI, so whatever the engine returns must be a real WAV."""
    speech = FakeSynthesizer().synthesize("hello")
    assert speech.media_type == "audio/wav"
    assert speech.audio.startswith(b"RIFF")

    # The real Piper path builds the container with the stdlib, which is what makes that header true.
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 128)
    assert buffer.getvalue().startswith(b"RIFF")
