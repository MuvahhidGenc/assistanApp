from hermes.agent.local_intent import guess_install_action


def test_guess_install_libreoffice():
    intent = guess_install_action("libreoffice i kur")
    assert intent is not None
    assert intent.request.name == "install_program"
    assert intent.request.arguments["package"] == "libreoffice"


def test_guess_install_chrome_kur():
    intent = guess_install_action("chrome kur")
    assert intent is not None
    assert intent.request.arguments["package"] == "chrome"
