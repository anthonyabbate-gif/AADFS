"""The app is unprotected on localhost by design; anywhere else it must not be."""

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aadfs.web import auth


def _app(password: str, username: str = auth.DEFAULT_USERNAME) -> TestClient:
    app = FastAPI()

    @app.get("/")
    def root():
        return {"ok": True}

    @app.get("/favicon.ico")
    def favicon():
        return {"icon": True}

    app.add_middleware(auth.BasicAuthMiddleware, password=password, username=username)
    return TestClient(app)


def _header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_no_credentials_is_challenged():
    response = _app("hunter2").get("/")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic realm=")


def test_correct_credentials_pass():
    response = _app("hunter2").get("/", headers=_header("aadfs", "hunter2"))
    assert response.status_code == 200


def test_wrong_password_is_rejected():
    assert _app("hunter2").get("/", headers=_header("aadfs", "nope")).status_code == 401


def test_wrong_username_is_rejected():
    assert _app("hunter2").get("/", headers=_header("root", "hunter2")).status_code == 401


def test_custom_username():
    client = _app("hunter2", username="anthony")
    assert client.get("/", headers=_header("anthony", "hunter2")).status_code == 200
    assert client.get("/", headers=_header("aadfs", "hunter2")).status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        {"Authorization": "Bearer abc"},
        {"Authorization": "Basic"},
        {"Authorization": "Basic !!!not-base64!!!"},
        {"Authorization": ""},
        {"Authorization": "Basic " + base64.b64encode(b"\xff\xfe").decode()},
    ],
)
def test_malformed_headers_are_rejected_not_crashed(header):
    assert _app("hunter2").get("/", headers=header).status_code == 401


def test_empty_password_in_a_request_is_rejected():
    assert _app("hunter2").get("/", headers=_header("aadfs", "")).status_code == 401


def test_favicon_stays_public_so_the_prompt_can_render():
    assert _app("hunter2").get("/favicon.ico").status_code == 200


# --- bind guard --------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_local_binds_need_no_password(host, monkeypatch):
    monkeypatch.delenv(auth.PASSWORD_ENV, raising=False)
    assert auth.guard_public_bind(host) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "100.64.0.3"])
def test_public_binds_are_refused_without_a_password(host, monkeypatch):
    monkeypatch.delenv(auth.PASSWORD_ENV, raising=False)
    message = auth.guard_public_bind(host)
    assert message is not None
    assert auth.PASSWORD_ENV in message  # tells you exactly how to fix it


def test_public_bind_is_allowed_once_a_password_is_set(monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, "hunter2")
    assert auth.guard_public_bind("0.0.0.0") is None


def test_a_blank_password_does_not_count_as_set(monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, "   ")
    assert auth.configured_password() is None
    assert auth.guard_public_bind("0.0.0.0") is not None


def test_install_is_a_no_op_without_a_password(monkeypatch):
    monkeypatch.delenv(auth.PASSWORD_ENV, raising=False)
    assert auth.install(FastAPI()) is False


def test_install_activates_with_a_password(monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, "hunter2")
    assert auth.install(FastAPI()) is True
