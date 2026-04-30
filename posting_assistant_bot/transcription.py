from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil
import subprocess
from posting_assistant_bot.logging_utils import log_event


class VoiceTranscriber:
    def __init__(self, *, model: str, enabled: bool, provider: str) -> None:
        self._model = model
        self._enabled = enabled
        self._provider = provider
        self._ready, self._not_ready_reason = self._run_preflight()

    def is_configured(self) -> bool:
        return self._enabled

    def is_ready(self) -> bool:
        return self._ready

    def not_ready_reason(self) -> str | None:
        return self._not_ready_reason

    def readiness_summary(self) -> str:
        if self._ready:
            return "ready"
        return f"not_ready: {self._not_ready_reason or 'unknown'}"

    async def transcribe_telegram_voice(self, input_path: Path) -> str:
        if not self._enabled:
            raise RuntimeError("Voice transcription is disabled.")
        if self._provider != "mlx-whisper":
            raise RuntimeError(f"Unsupported transcription provider: {self._provider}")
        if not self._ready:
            raise RuntimeError(f"voice_not_ready: {self._not_ready_reason or 'unknown reason'}")

        wav_path = input_path.with_suffix(".wav")
        log_event(
            logging.getLogger(__name__),
            level=logging.DEBUG,
            component="transcription.voice",
            event="voice_conversion_started",
            message="Voice conversion to wav started",
            context={"input_path": str(input_path), "output_path": str(wav_path)},
        )
        try:
            await asyncio.to_thread(_convert_voice_to_wav_ffmpeg, input_path, wav_path)
            log_event(
                logging.getLogger(__name__),
                level=logging.DEBUG,
                component="transcription.voice",
                event="voice_conversion_completed",
                message="Voice conversion to wav completed",
                context={"output_path": str(wav_path)},
            )
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="transcription.voice",
                event="voice_transcription_started",
                message="Local voice transcription started",
                context={"model": self._model},
            )
            transcript = await asyncio.to_thread(_transcribe_wav_local, wav_path, self._model)
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="transcription.voice",
                event="voice_transcription_completed",
                message="Local voice transcription completed",
                context={"model": self._model, "transcript_length": len(transcript)},
            )
            return transcript
        except Exception as exc:
            event_name = "voice_transcription_failed"
            if "ffmpeg_not_found" in str(exc):
                event_name = "ffmpeg_not_found"
            elif "voice_conversion_failed" in str(exc):
                event_name = "voice_conversion_failed"
            elif "voice_model_load_failed" in str(exc):
                event_name = "voice_model_load_failed"
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="transcription.voice",
                event=event_name,
                message="Voice transcription failed",
                context={"model": self._model, "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )
            raise

    def _run_preflight(self) -> tuple[bool, str | None]:
        if not self._enabled:
            return False, "voice_transcription_disabled"
        if self._provider != "mlx-whisper":
            return False, f"unsupported_provider:{self._provider}"
        if shutil.which("ffmpeg") is None:
            return False, "ffmpeg_not_found: ffmpeg is not installed or not available in PATH."
        try:
            import mlx_whisper  # type: ignore

            del mlx_whisper
        except ImportError:
            return False, "voice_model_load_failed: Local transcription backend is not installed."
        return True, None


def _convert_voice_to_wav_ffmpeg(input_path: Path, output_path: Path) -> None:
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(output_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg_not_found: ffmpeg is not installed or not available in PATH.") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"voice_conversion_failed: ffmpeg conversion failed with code {result.returncode}: {result.stderr.strip()}"
        )


def _transcribe_wav_local(wav_path: Path, model: str) -> str:
    try:
        import mlx_whisper
    except ImportError as exc:
        raise RuntimeError("voice_model_load_failed: Local transcription backend is not installed.") from exc

    result = mlx_whisper.transcribe(str(wav_path), path_or_hf_repo=model)
    transcript = str(result.get("text", "")).strip()
    if not transcript:
        raise RuntimeError("voice_transcription_empty: Local transcription returned an empty transcript.")
    return transcript
