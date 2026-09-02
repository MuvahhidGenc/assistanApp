from hermes.agent.local_intent import match_local_intent
from hermes.agent.task_planner import has_actionable_sequence, plan_local_sequence, split_command_steps


def test_split_command_steps_turkish():
    parts = split_command_steps("dns degistir google yap ve chrome ac")
    assert len(parts) == 2
    assert "dns" in parts[0]
    assert "chrome" in parts[1]


def test_plan_local_sequence_multi_step():
    steps = plan_local_sequence("dns degistir cloudflare yap ve chrome ac")
    assert len(steps) >= 2
    assert steps[0].request.name == "set_dns"
    assert steps[1].request.name == "open_app"


def test_has_actionable_sequence():
    assert has_actionable_sequence("Deneme klasoru olustur ve chrome ac") is True
    assert has_actionable_sequence("merhaba nasilsin") is False


def test_match_video_open_youtube():
    intent = match_local_intent("youtube ac")
    assert intent is not None
    assert intent.request.name == "open_url"
    assert "youtube" in intent.request.arguments["url"]


def test_match_video_with_url():
    intent = match_local_intent("su videoyu ac https://youtu.be/dQw4w9WgXcQ")
    assert intent is not None
    assert intent.request.name == "open_url"
    assert "youtu.be" in intent.request.arguments["url"]


def test_match_youtube_search_tevhid():
    intent = match_local_intent(
        "YouTube'dan Tevhid ile aramaya ac arama yap ve bir video ac"
    )
    assert intent is not None
    assert intent.request.name == "open_url"
    url = intent.request.arguments["url"]
    assert "search_query=" in url
    assert "Tevhid" in url or "tevhid" in url.lower()


def test_match_create_folder():
    intent = match_local_intent("masaustunde Proje klasoru olustur")
    assert intent is not None
    assert intent.request.name == "create_folder"
