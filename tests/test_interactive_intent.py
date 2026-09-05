from hermes.agent.local_intent import match_local_intent


def test_match_scroll_down():
    intent = match_local_intent("asagi in")
    assert intent is not None
    assert intent.request.name == "scroll"
    assert intent.request.arguments.get("direction") == "down"


def test_match_show_desktop():
    intent = match_local_intent("masaustunu goster")
    assert intent is not None
    assert intent.request.name == "show_desktop"


def test_match_browser_back():
    intent = match_local_intent("geri git")
    assert intent is not None
    assert intent.request.name == "browser_nav"
    assert intent.request.arguments.get("action") == "back"


def test_match_click_video_title():
    intent = match_local_intent("Tevhid videosunu ac")
    assert intent is None or intent.request.name != "click_text"


def test_match_literal_visible_label_still_click_text():
    intent = match_local_intent("Abone ol yazisina tikla")
    assert intent is not None
    assert intent.request.name == "click_text"
    assert "Abone" in intent.request.arguments.get("text", "")


def test_spatial_video_reference_is_not_literal_click_text():
    intent = match_local_intent("ortadaki videoyu ac")
    assert intent is None or intent.request.name != "click_text"


def test_match_read_screen_results():
    intent = match_local_intent("ekranda ne goruyorsun")
    assert intent is not None
    assert intent.request.name == "read_screen_text"
