"""Local speech recognition and speech synthesis.

Both run on this machine from model files on disk. Nothing here calls a hosted speech service: if a model is
missing, local mode says which file to download rather than reaching for a cloud API.

Recognition is deliberately conservative. Empty or unintelligible audio raises rather than returning a guess,
because a guessed policy number or procedure code would flow straight into a rule check.
"""

import io
import logging
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from preauth.local.config import LocalSettings, SttProvider, TtsProvider
from preauth.local.errors import LocalDependencyMissingError, SpeechNotRecognisedError

logger = logging.getLogger("preauth.local.speech")

# Codes and references are spoken back character by character, so the recogniser needs the terms it will hear.
KEYTERM_PROMPT = (
    "Sawt Assurance pre-authorisation. Terms: provider number PRV, policy number POL-SA, procedure code SP, "
    "case reference PA, onboarding reference ONB-APP, AED, pre-authorisation, Basic, Enhanced, Comprehensive, "
    "Executive, Dubai, Abu Dhabi, Sharjah, eClaimLink, Shafafiya, Emirates ID."
)


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None
    duration_seconds: float | None


@dataclass(frozen=True)
class Speech:
    audio: bytes
    media_type: str


class Transcriber(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    def check(self) -> None: ...

    def transcribe(self, audio: bytes, language: str | None = None) -> Transcription: ...


class Synthesizer(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    def check(self) -> None: ...

    def synthesize(self, text: str) -> Speech: ...


# --------------------------------------------------------------------------- disabled stand-ins


@dataclass(frozen=True)
class DisabledTranscriber:
    """Text-only local mode: the browser and CLI still work, the microphone button does not."""

    reason: str = "Speech recognition is disabled (PREAUTH_LOCAL_STT_PROVIDER=none). Type instead of speaking."

    @property
    def name(self) -> str:
        return "disabled"

    @property
    def enabled(self) -> bool:
        return False

    def check(self) -> None:
        return None

    def transcribe(self, audio: bytes, language: str | None = None) -> Transcription:
        raise LocalDependencyMissingError(self.reason, code="SPEECH_INPUT_DISABLED")


@dataclass(frozen=True)
class DisabledSynthesizer:
    reason: str = "Speech synthesis is disabled (PREAUTH_LOCAL_TTS_PROVIDER=none). Replies are text only."

    @property
    def name(self) -> str:
        return "disabled"

    @property
    def enabled(self) -> bool:
        return False

    def check(self) -> None:
        return None

    def synthesize(self, text: str) -> Speech:
        raise LocalDependencyMissingError(self.reason, code="SPEECH_OUTPUT_DISABLED")


# --------------------------------------------------------------------------- faster-whisper


@dataclass
class FasterWhisperTranscriber:
    """OpenAI's Whisper weights run locally through CTranslate2. No API key, no network at inference time."""

    settings: LocalSettings
    _model: Any = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return f"faster-whisper:{self.settings.stt_model}"

    @property
    def enabled(self) -> bool:
        return True

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ModuleNotFoundError as e:
            raise LocalDependencyMissingError(
                "faster-whisper is not installed",
                details={"install": "uv sync --extra local-voice"},
            ) from e
        source = self.settings.stt_model_dir or self.settings.stt_model
        if self.settings.stt_model_dir and not Path(self.settings.stt_model_dir).is_dir():
            raise LocalDependencyMissingError(
                f"PREAUTH_LOCAL_STT_MODEL_DIR points at {self.settings.stt_model_dir!r}, which is not a directory"
            )
        logger.info("stt_model_loading", extra={"model": source, "compute_type": self.settings.stt_compute_type})
        self._model = WhisperModel(source, device="cpu", compute_type=self.settings.stt_compute_type)
        return self._model

    def check(self) -> None:
        self._load()

    def transcribe(self, audio: bytes, language: str | None = None) -> Transcription:
        if not audio:
            raise SpeechNotRecognisedError("No audio was received. Ask the caller to repeat.")
        model = self._load()
        # faster-whisper decodes container formats (webm/opus from the browser) through PyAV, from a path.
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=True) as handle:
            handle.write(audio)
            handle.flush()
            segments, info = model.transcribe(
                handle.name,
                language=language,
                vad_filter=True,
                initial_prompt=KEYTERM_PROMPT,
                beam_size=5,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise SpeechNotRecognisedError(
                "No speech was recognised in that recording. Ask the caller to repeat.",
                details={"duration_seconds": getattr(info, "duration", None)},
            )
        return Transcription(
            text=text, language=getattr(info, "language", None), duration_seconds=getattr(info, "duration", None)
        )


# --------------------------------------------------------------------------- piper


@dataclass
class PiperSynthesizer:
    """Piper: a small ONNX neural voice that runs on CPU from one .onnx file plus its .onnx.json config."""

    settings: LocalSettings
    _voice: Any = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return f"piper:{Path(self.settings.tts_voice_path).name if self.settings.tts_voice_path else 'unset'}"

    @property
    def enabled(self) -> bool:
        return True

    def _load(self):
        if self._voice is not None:
            return self._voice
        path = self.settings.tts_voice_path
        if not path:
            raise LocalDependencyMissingError(
                "PREAUTH_LOCAL_TTS_VOICE is not set; it must point at a Piper .onnx voice file",
                details={"download": "python -m piper.download_voices en_GB-alba-medium"},
            )
        if not Path(path).is_file():
            raise LocalDependencyMissingError(
                f"Piper voice file {path!r} does not exist",
                details={"download": "python -m piper.download_voices en_GB-alba-medium"},
            )
        if not Path(f"{path}.json").is_file():
            raise LocalDependencyMissingError(
                f"Piper voice config {path}.json is missing; it must sit beside the .onnx file"
            )
        try:
            from piper import PiperVoice
        except ModuleNotFoundError as e:
            raise LocalDependencyMissingError(
                "piper-tts is not installed",
                details={"install": "uv sync --extra local-voice"},
            ) from e
        logger.info("tts_voice_loading", extra={"voice": path})
        self._voice = PiperVoice.load(path)
        return self._voice

    def check(self) -> None:
        self._load()

    def synthesize(self, text: str) -> Speech:
        voice = self._load()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            # Piper 1.3+ writes the header itself; older releases yield raw chunks and expect the caller to.
            if hasattr(voice, "synthesize_wav"):
                voice.synthesize_wav(text, wav)
            else:  # pragma: no cover - depends on the installed piper release
                chunks = list(voice.synthesize(text))
                first = chunks[0]
                wav.setnchannels(getattr(first, "sample_channels", 1))
                wav.setsampwidth(getattr(first, "sample_width", 2))
                wav.setframerate(getattr(first, "sample_rate", 22050))
                for chunk in chunks:
                    wav.writeframes(chunk.audio_int16_bytes)
        return Speech(audio=buffer.getvalue(), media_type="audio/wav")


def build_transcriber(settings: LocalSettings) -> Transcriber:
    if settings.stt_provider is SttProvider.FASTER_WHISPER:
        return FasterWhisperTranscriber(settings)
    return DisabledTranscriber()


def build_synthesizer(settings: LocalSettings) -> Synthesizer:
    if settings.tts_provider is TtsProvider.PIPER:
        return PiperSynthesizer(settings)
    return DisabledSynthesizer()
