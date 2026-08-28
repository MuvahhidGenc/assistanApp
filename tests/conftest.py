import pytest

from hermes.utils.logging import setup_cli_logging


@pytest.fixture(autouse=True)
def _reset_logging():
    setup_cli_logging(debug=False)
    yield
