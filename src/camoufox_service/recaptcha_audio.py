"""音频下载、格式转换、语音识别与文本规范化。"""

from __future__ import annotations

import os
import re
import shutil
from io import BytesIO

import requests

from .models import ProxyConfig


class AudioChallengeError(RuntimeError):
    pass


class AudioDownloadError(AudioChallengeError):
    pass


class AudioRecognitionError(AudioChallengeError):
    pass


class AudioChallengeProcessor:
    """复用浏览器身份下载音频，并完成 WAV 转换与语音识别。"""

    def __init__(
        self,
        *,
        user_agent: str,
        audio_cache: dict[str, bytes],
        page,
        proxy: ProxyConfig | None = None,
        request_timeout: float = 15,
    ):
        self.user_agent = user_agent
        self.audio_cache = audio_cache
        self.page = page
        self.request_timeout = request_timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = user_agent
        if proxy:
            proxy_url = proxy.requests_url()
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        self._configure_audio_tools()

    def close(self) -> None:
        self.session.close()

    def try_recognize_from_url(
        self,
        audio_url: str,
        *,
        language: str = "en-US",
        timeout: float | None = None,
    ) -> str | None:
        """依次下载、转换和识别音频，无法识别时返回空结果。"""

        audio = self.download_audio(audio_url, timeout=timeout)
        wav = self.convert_mp3_to_wav(audio)
        return self.transcribe(wav, language=language) if wav else None

    def download_audio(self, url: str, *, timeout: float | None = None) -> bytes:
        """依次尝试响应缓存、浏览器请求上下文和独立 HTTP Session。"""

        cached = self.audio_cache.get(url)
        if cached:
            return cached
        if self.page is not None:
            try:
                response = self.page.context.request.get(
                    url,
                    headers={
                        "Accept": "audio/*,*/*;q=0.8",
                        "Referer": "https://www.google.com/recaptcha/api2/bframe",
                        "User-Agent": self.user_agent,
                    },
                    timeout=int((timeout or self.request_timeout) * 1000),
                )
                if response.ok:
                    return response.body()
            except Exception:
                pass
        try:
            response = self.session.get(
                url,
                headers={
                    "Accept": "audio/*,*/*;q=0.8",
                    "Referer": "https://www.google.com/recaptcha/api2/bframe",
                },
                timeout=timeout or self.request_timeout,
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            raise AudioDownloadError("audio download failed") from exc

    @staticmethod
    def convert_mp3_to_wav(mp3_bytes: bytes) -> bytes | None:
        """把 MP3 规范化为单声道 16 kHz WAV。"""

        if not mp3_bytes or len(mp3_bytes) < 16:
            return None
        from pydub import AudioSegment, effects
        from pydub.exceptions import CouldntDecodeError

        try:
            sound = AudioSegment.from_mp3(BytesIO(mp3_bytes))
        except CouldntDecodeError:
            return None
        sound = effects.normalize(sound).set_channels(1).set_frame_rate(16_000)
        output = BytesIO()
        sound.export(output, format="wav")
        return output.getvalue()

    @classmethod
    def transcribe(cls, wav_bytes: bytes, *, language: str) -> str | None:
        """调用语音识别服务，并返回规范化后的答案文本。"""

        import speech_recognition

        recognizer = speech_recognition.Recognizer()
        with speech_recognition.AudioFile(BytesIO(wav_bytes)) as source:
            audio = recognizer.record(source)
        try:
            text = recognizer.recognize_google(audio, language=cls.normalize_language(language))
        except speech_recognition.UnknownValueError:
            return None
        except speech_recognition.RequestError as exc:
            raise AudioRecognitionError("speech recognition request failed") from exc
        return cls.normalize_transcript(text) or None

    @staticmethod
    def normalize_language(language: str) -> str:
        aliases = {
            "en": "en-US",
            "zh": "zh-CN",
            "de": "de-DE",
            "es": "es-ES",
            "fr": "fr-FR",
            "it": "it-IT",
            "ja": "ja-JP",
            "ko": "ko-KR",
            "pt": "pt-BR",
            "ru": "ru-RU",
        }
        value = str(language or "en-US").strip().replace("_", "-")
        return aliases.get(value.lower(), value or "en-US")

    @staticmethod
    def normalize_transcript(text: str) -> str:
        value = re.sub(r"[^0-9a-zA-Z\s]+", " ", str(text or "").strip().lower())
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _find_tool(name: str) -> str | None:
        configured = os.getenv(f"{name.upper()}_PATH")
        candidates = (configured, shutil.which(name), shutil.which(f"{name}.exe"))
        return next((path for path in candidates if path and os.path.exists(path)), None)

    @classmethod
    def _configure_audio_tools(cls) -> None:
        import pydub

        ffmpeg = cls._find_tool("ffmpeg")
        ffprobe = cls._find_tool("ffprobe")
        if not ffmpeg or not ffprobe:
            raise AudioChallengeError("ffmpeg and ffprobe are required")
        pydub.AudioSegment.converter = ffmpeg
        pydub.utils.get_prober_name = lambda: ffprobe
