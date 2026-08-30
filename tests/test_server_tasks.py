from hermes.agent.server_tasks import should_defer_to_server


def test_youtube_tasks_stay_local():
    assert should_defer_to_server("YouTube ac Tevhid ara video izle") is False
    assert should_defer_to_server("chrome ac google.com") is False


def test_download_still_defers():
    assert should_defer_to_server("chrome indir ve kur") is True
