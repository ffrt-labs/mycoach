"""GarminClient.connect: recovering a session Garmin refused, and saying why a login failed.

The fake stands in for ``garminconnect.Garmin``. Its errors are shaped like the library's
own (0.3.2): a refused saved session surfaces as ``GarminConnectAuthenticationError(
"Failed to retrieve social profile")`` caused by a 401 from the API, because the library
keeps presenting the dead token instead of falling back to the password.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from garminconnect import (  # type: ignore[import-untyped]
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from mycoach.sources.garmin import client as client_module
from mycoach.sources.garmin.auth import GarminAuth
from mycoach.sources.garmin.client import (
    LOCK_FILE,
    REJECTED_TOKEN_FILE,
    TOKEN_FILE,
    GarminClient,
    _token_lock,
)


def _refused_session() -> GarminConnectAuthenticationError:
    error = GarminConnectAuthenticationError("Failed to retrieve social profile")
    error.__cause__ = GarminConnectConnectionError("API Error 401 - ")
    return error


def _unreachable() -> GarminConnectAuthenticationError:
    error = GarminConnectAuthenticationError("Failed to retrieve social profile")
    error.__cause__ = GarminConnectConnectionError("Connection reset by peer")
    return error


class _FakeGarmin:
    """Each ``login`` takes the next outcome off ``outcomes``: an exception, or success."""

    outcomes: list[BaseException | None] = []
    logins: list[bool] = []
    """Per login, whether a saved session was on disk when it started."""

    def __init__(self, email: str, password: str) -> None:
        self.display_name = "athlete"

    def login(self, tokenstore: str) -> None:
        token_file = Path(tokenstore) / TOKEN_FILE
        _FakeGarmin.logins.append(token_file.exists())
        outcome = _FakeGarmin.outcomes.pop(0)
        if outcome is not None:
            raise outcome
        token_file.write_text('{"di_token": "fresh"}')


@pytest.fixture
def garmin(monkeypatch: pytest.MonkeyPatch) -> type[_FakeGarmin]:
    _FakeGarmin.outcomes = []
    _FakeGarmin.logins = []
    monkeypatch.setattr(client_module, "Garmin", _FakeGarmin)
    return _FakeGarmin


@pytest.fixture
def token_dir(tmp_path: Path) -> Path:
    path = tmp_path / "tokens"
    path.mkdir()
    return path


def _client(token_dir: Path) -> GarminClient:
    return GarminClient(GarminAuth("athlete@example.com", "secret", token_dir))


def _save_session(token_dir: Path) -> None:
    (token_dir / TOKEN_FILE).write_text('{"di_token": "dead"}')


def test_a_refused_session_is_moved_aside_and_replaced_by_a_password_login(
    garmin: type[_FakeGarmin], token_dir: Path
) -> None:
    _save_session(token_dir)
    garmin.outcomes = [_refused_session(), None]

    client = _client(token_dir)

    assert client.connect() is True
    assert client.last_error is None
    assert garmin.logins == [True, False]
    assert (token_dir / REJECTED_TOKEN_FILE).read_text() == '{"di_token": "dead"}'
    assert (token_dir / TOKEN_FILE).read_text() == '{"di_token": "fresh"}'


def test_a_refused_refresh_token_also_falls_back_to_the_password(
    garmin: type[_FakeGarmin], token_dir: Path
) -> None:
    _save_session(token_dir)
    refused = GarminConnectAuthenticationError("Failed to retrieve social profile")
    refused.__cause__ = GarminConnectAuthenticationError(
        'DI token refresh failed: 400 {"error":"invalid_grant"}'
    )
    garmin.outcomes = [refused, None]

    assert _client(token_dir).connect() is True
    assert garmin.logins == [True, False]


def test_the_password_login_is_tried_only_once(garmin: type[_FakeGarmin], token_dir: Path) -> None:
    """Each attempt counts against Garmin's login rate limit."""
    _save_session(token_dir)
    garmin.outcomes = [
        _refused_session(),
        GarminConnectAuthenticationError("401 Unauthorized (Invalid Username or Password)"),
    ]

    client = _client(token_dir)

    assert client.connect() is False
    assert len(garmin.logins) == 2
    assert client.last_error == "Garmin rejected the email or password"


def test_an_unreachable_garmin_keeps_the_saved_session(
    garmin: type[_FakeGarmin], token_dir: Path
) -> None:
    """A network failure says nothing about the session, so it must not be thrown away."""
    _save_session(token_dir)
    garmin.outcomes = [_unreachable()]

    client = _client(token_dir)

    assert client.connect() is False
    assert garmin.logins == [True]
    assert (token_dir / TOKEN_FILE).read_text() == '{"di_token": "dead"}'
    assert not (token_dir / REJECTED_TOKEN_FILE).exists()
    assert client.last_error == (
        "GarminConnectAuthenticationError: Failed to retrieve social profile "
        "(Connection reset by peer)"
    )


def test_rate_limiting_is_named_as_such(garmin: type[_FakeGarmin], token_dir: Path) -> None:
    garmin.outcomes = [GarminConnectTooManyRequestsError("Mobile login returned 429")]

    client = _client(token_dir)

    assert client.connect() is False
    assert client.last_error is not None
    assert "rate limiting" in client.last_error


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ([None], True),
        ([GarminConnectAuthenticationError("Username and password are required")], False),
    ],
)
def test_a_first_login_with_no_saved_session_is_one_attempt(
    garmin: type[_FakeGarmin], token_dir: Path, outcomes: list[Any], expected: bool
) -> None:
    garmin.outcomes = outcomes

    assert _client(token_dir).connect() is expected
    assert garmin.logins == [False]


def test_a_successful_connect_clears_the_previous_failure(
    garmin: type[_FakeGarmin], token_dir: Path
) -> None:
    garmin.outcomes = [GarminConnectTooManyRequestsError("429"), None]
    client = _client(token_dir)

    assert client.connect() is False
    assert client.connect() is True
    assert client.last_error is None


def test_logins_wait_for_each_other(garmin: type[_FakeGarmin], token_dir: Path) -> None:
    """Two logins spending the same refresh token can get the session revoked."""
    garmin.outcomes = [None]
    released = threading.Event()

    def hold_the_lock() -> None:
        with _token_lock(token_dir):
            released.wait(timeout=5)

    holder = threading.Thread(target=hold_the_lock)
    holder.start()
    while not (token_dir / LOCK_FILE).exists():
        time.sleep(0.01)
    time.sleep(0.05)

    finished: list[bool] = []
    waiter = threading.Thread(target=lambda: finished.append(_client(token_dir).connect()))
    waiter.start()
    time.sleep(0.3)
    assert finished == [] and garmin.logins == []

    released.set()
    holder.join()
    waiter.join(timeout=5)
    assert finished == [True]


def test_a_login_that_cannot_get_the_lock_gives_up_with_a_reason(
    garmin: type[_FakeGarmin], token_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "LOCK_TIMEOUT_SECONDS", 0.2)
    garmin.outcomes = [None]

    with _token_lock(token_dir):
        result: list[GarminClient] = []
        thread = threading.Thread(target=lambda: result.append(_connected(token_dir)))
        thread.start()
        thread.join(timeout=5)

    client = result[0]
    assert client.last_error is not None
    assert "another Garmin login" in client.last_error
    assert garmin.logins == []


def _connected(token_dir: Path) -> GarminClient:
    client = _client(token_dir)
    client.connect()
    return client


def test_the_garmin_client_imports_on_its_own() -> None:
    """``import mycoach.sources.garmin.client`` first used to hit a circular import."""
    env = {**os.environ, "MYCOACH_OPENROUTER_API_KEY": "test"}
    for module in ("mycoach.sources.garmin.client", "mycoach.sources.intake", "mycoach.health"):
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert completed.returncode == 0, f"import {module} failed:\n{completed.stderr}"
