from hermes.agent.local_intent import match_local_intent, summarize_local_result
from hermes.tools.base import ToolExecutionResult


def test_match_dns_change_google():
    intent = match_local_intent("dns degistir google yap")
    assert intent is not None
    assert intent.request.name == "set_dns"
    assert intent.request.arguments.get("preset") == "google"


def test_match_dns_explicit_ips():
    intent = match_local_intent("dns'i 1.1.1.1 ve 1.0.0.1 yap")
    assert intent is not None
    assert intent.request.name == "set_dns"
    servers = intent.request.arguments.get("servers") or []
    assert "1.1.1.1" in servers


def test_match_screenshot_and_network():
    shot = match_local_intent("ekran goruntusu al")
    assert shot is not None
    assert shot.request.name == "screenshot"
    net = match_local_intent("ip adresimi goster")
    assert net is not None
    assert net.request.name == "get_network_config"


def test_match_screen_read_phrases():
    for phrase in ("ekrani oku", "ekrana bak", "ne goruyorsun", "ekranda ne yaziyor"):
        intent = match_local_intent(phrase)
        assert intent is not None, phrase
        assert intent.request.name == "read_screen_text"


def test_match_ignores_chat_and_multi_step_chrome():
    assert match_local_intent("Az once ne dedim") is None
    assert match_local_intent("Chrome ac ve google.com'a git") is None


def test_summarize_dns_success():
    intent = match_local_intent("dns degistir")
    assert intent is not None
    text = summarize_local_result(
        intent,
        ToolExecutionResult(success=True, output={"adapter": "Wi-Fi", "servers": ["8.8.8.8"]}),
    )
    assert "DNS" in text
    assert "8.8.8.8" in text
