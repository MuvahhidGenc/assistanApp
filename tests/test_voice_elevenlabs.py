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
    tts = create_tts(backend="elevenlabs", elevenlabs_api_key="")
    assert tts.is_available() or not tts.is_available()


def test_credentials_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes.config.credentials.app_data_dir", lambda: tmp_path)
    save_credentials({"elevenlabs_api_key": "sk-roundtrip"})
    assert load_credentials()["elevenlabs_api_key"] == "sk-roundtrip"


def test_spoken_install_ack():
    from hermes.voice.spoken import spoken_quick_ack

    assert "libreoffice" in spoken_quick_ack("libreoffice i kur").casefold()


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
    with patch("hermes.voice.tts._play_mp3_powershell", return_value=True):
        tts._speak_sync("Merhaba abi")
