"""V3 contract: secret-shaped values must never reach the LLM prompt.

The ``ReasoningRuntime`` carries every event payload through
``_scrub_payload`` before handing it to the LLM transport. This test
pins that contract with explicit positive and negative cases.

If a future change accidentally drops the scrubber, these tests
fail before a secret can leak into a real prompt.
"""

from __future__ import annotations

import pytest

from hermes.reasoning.runtime import _scrub_payload, _scrub_value, _looks_like_secret


SECRETS = [
    "password=hunter2",
    "Password: secret",
    "api_key=sk-1234567890abcdefABCDEF",
    "API-KEY: abcdefghijklmnopqrst",
    "secret=blah",
    "token=ghp_1234567890abcdefghijklmn",
    "ghp_1234567890abcdefghij",
    "sk-abcdef1234567890",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIE...",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_looks_like_secret_detects_known_shapes(secret):
    assert _looks_like_secret(secret) is True


def test_looks_like_secret_does_not_flag_normal_strings():
    assert _looks_like_secret("hello world") is False
    assert _looks_like_secret("Yarın saat 14:00'te buluşalım") is False
    assert _looks_like_secret("file=/tmp/x.txt") is False


@pytest.mark.parametrize("secret", SECRETS)
def test_scrub_value_redacts_known_shapes(secret):
    out = _scrub_value(secret)
    assert out == "[REDACTED]"


def test_scrub_value_truncates_oversized_strings():
    big = "a" * 5000
    out = _scrub_value(big, max_len=2000)
    assert len(out) < 2100  # marker adds a small amount of text
    assert "[truncated" in out


def test_scrub_value_passes_through_safe_strings():
    assert _scrub_value("normal text") == "normal text"
    assert _scrub_value("") == ""


def test_scrub_payload_walks_dicts_and_lists_recursively():
    payload = {
        "user": "Ömer",
        "tool_output": "Yankı: password=foo",
        "nested": {
            "deep": ["another", "token=ghp_abcdefghij12345678"],
        },
        "scalar": 42,
        "flag": True,
    }
    scrubbed = _scrub_payload(payload)

    def walk(obj, path=()):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, path + (k,))
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                walk(v, path + (i,))
        elif isinstance(obj, str):
            # No raw secret text remains anywhere in the structure.
            for needle in ("password=", "token=", "api_key=", "sk-", "ghp_"):
                assert needle not in obj, f"leaked at {path}: {obj!r}"

    walk(scrubbed)
    # Safe scalars pass through unchanged.
    assert scrubbed["scalar"] == 42
    assert scrubbed["flag"] is True
    # User-provided non-secret text remains.
    assert scrubbed["user"] == "Ömer"


def test_scrub_payload_does_not_mutate_input():
    payload = {
        "list": ["password=secret", "normal text"],
        "tuple": ("token=ghp_abc123",),
    }
    snapshot = {
        "list": list(payload["list"]),
        "tuple": tuple(payload["tuple"]),
    }
    _ = _scrub_payload(payload)
    # Input is not mutated; scrubber returns a new structure.
    assert payload["list"] == snapshot["list"]
    assert payload["tuple"] == snapshot["tuple"]
