from unittest.mock import patch

from hermes.config.credentials import load_credentials, save_credentials
from hermes.voice.tts import ElevenLabsTTS, create_tts


def test_elevenlabs_tts_requires_api_key():
    tts = ElevenLabsTTS(api_key="")
    assert not tts.is_available()


def test_create_tts_elevenlabs_with_key():
    tts = create_tts(backend="elevenlabs", elevenlabs_api_key="sk-test-key")
    assert tts.is_available()


def test_create_tts_falls_back_to_edge_without_key():
    from hermes.voice.tts import EdgeTurkishTTS, FallbackTextToSpeech

    tts = create_tts(backend="elevenlabs", elevenlabs_api_key="")
    assert isinstance(tts, (EdgeTurkishTTS, FallbackTextToSpeech))
    assert tts.is_available()


def test_create_tts_missing_key_returns_edge_or_null():
    from hermes.config.credentials import elevenlabs_key_hint

    tts = create_tts(backend="elevenlabs", elevenlabs_api_key="")
    hint = elevenlabs_key_hint()
    assert "ElevenLabs API anahtari bulunamadi" in hint
    assert "credentials.yaml" in hint


def test_credentials_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes.config.credentials.app_data_dir", lambda: tmp_path)
    save_credentials({"elevenlabs_api_key": "sk-roundtrip"})
    assert load_credentials()["elevenlabs_api_key"] == "sk-roundtrip"


def test_resolve_elevenlabs_api_key_from_env(monkeypatch):
    from hermes.config.credentials import resolve_elevenlabs_api_key

    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-from-env")
    key, source = resolve_elevenlabs_api_key()
    assert key == "sk-from-env"
    assert source == "environment"


def test_resolve_elevenlabs_api_key_missing(monkeypatch, tmp_path):
    from hermes.config.credentials import resolve_elevenlabs_api_key

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr("hermes.config.credentials.app_data_dir", lambda: tmp_path)
    key, source = resolve_elevenlabs_api_key()
    assert key == ""
    assert source == "missing"


def test_fallback_skips_unavailable_elevenlabs(monkeypatch):
    from hermes.voice.tts import FallbackTextToSpeech

    class FakeEdge:
        spoken: list[str] = []

        def is_available(self) -> bool:
            return True

        async def speak(self, text: str) -> None:
            FakeEdge.spoken.append(text)

        def stop(self) -> None:
            return None

    fake_edge = FakeEdge()
    tts = FallbackTextToSpeech(ElevenLabsTTS(api_key=""), fake_edge)
    import asyncio

    asyncio.run(tts.speak("Merhaba"))
    assert FakeEdge.spoken == ["Merhaba"]


def test_elevenlabs_auth_error_logged(monkeypatch):
    import httpx

    tts = ElevenLabsTTS(api_key="sk-bad", voice_id="voice123")

    class FakeResponse:
        status_code = 401
        text = '{"detail":{"status":"invalid_api_key"}}'

        def raise_for_status(self):
            request = httpx.Request("POST", "https://api.elevenlabs.io")
            raise httpx.HTTPStatusError("401", request=request, response=self)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json, headers):
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)
    try:
        tts._synthesize_mp3("Merhaba")
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "401" in str(exc)
    assert raised


def test_elevenlabs_speak_uses_http(monkeypatch, tmp_path):
    tts = ElevenLabsTTS(api_key="sk-test", voice_id="voice123")

    class FakeResponse:
        content = b"\xff\xfb" + b"\x00" * 128
        status_code = 200

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json, headers):
            assert "voice123" in url
            assert headers["xi-api-key"] == "sk-test"
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)

    async def _run() -> None:
        with patch("hermes.voice.tts._play_mp3_powershell"):
            await tts.speak("Merhaba abi")

    import asyncio

    asyncio.run(_run())
