"""Operator import of an existing Instagram token (app.instagram_import): same checks and storage as a connect,
against the fake Meta from test_instagram_connect; the token is never printed."""

import httpx
import pytest
from test_instagram_connect import FakeMeta, audit_actions, rows, set_pilot, vault_count

from app import instagram_api, instagram_import


@pytest.fixture
def owned(monkeypatch, sign_in):
    m = FakeMeta()
    monkeypatch.setattr(instagram_api, "transport", httpx.MockTransport(m.handler))
    monkeypatch.setenv("IG_ACCESS_TOKEN", m.long)
    monkeypatch.setenv("IG_USER_ID", m.user_id)
    c = sign_in(org="Our Own")
    set_pilot(c.org_id)
    return c, m


def test_imports_the_token_into_vault_for_a_pilot(owned, capsys):
    c, m = owned
    instagram_import.main([str(c.org_id)])
    out = capsys.readouterr().out
    assert f"@{m.username}" in out and m.long not in out
    [(_, status, token_ref, row)] = rows(c.org_id)
    assert status == "connected" and token_ref and m.long not in row
    assert vault_count(f"instagram/{c.org_id}/") == 1
    assert audit_actions(c.org_id)[-1] == "instagram.connected"


def test_refuses_a_non_pilot(owned):
    c, _ = owned
    set_pilot(c.org_id, on=False)
    with pytest.raises(SystemExit, match="isn't a Pilot"):
        instagram_import.main([str(c.org_id)])
    assert rows(c.org_id) == []


def test_refuses_a_personal_account(owned):
    c, m = owned
    m.account_type = "PERSONAL"
    with pytest.raises(SystemExit, match="personal account"):
        instagram_import.main([str(c.org_id)])
    assert rows(c.org_id) == []


def test_refuses_a_token_for_another_account(owned, monkeypatch):
    c, _ = owned
    monkeypatch.setenv("IG_USER_ID", "17840000000000001")
    with pytest.raises(SystemExit, match="not IG_USER_ID"):
        instagram_import.main([str(c.org_id)])
    assert rows(c.org_id) == []


def test_needs_a_token(owned, monkeypatch):
    c, _ = owned
    monkeypatch.setenv("IG_ACCESS_TOKEN", "")
    monkeypatch.setattr(instagram_import, "dotenv_values", lambda *_: {})
    with pytest.raises(SystemExit, match="IG_ACCESS_TOKEN isn't set"):
        instagram_import.main([str(c.org_id)])
