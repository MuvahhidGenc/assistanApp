from hermes.agent.local_intent import guess_file_action


def test_guess_file_action_word_tevhid():
    intent = guess_file_action(
        "hermes2 klasorune tevhid word olustur ve tevhid ile ilgili detayli icerik gir"
    )
    assert intent is not None
    assert intent.request.name == "create_word_document"
    assert "tevhid" in intent.request.arguments["path"].casefold()
    assert "tevhid" in intent.request.arguments["content"].casefold()


def test_guess_file_action_delete_folder():
    intent = guess_file_action("Hermes klasorunu sil")
    assert intent is not None
    assert intent.request.name == "delete_path"
    assert intent.request.arguments.get("recursive") is True
