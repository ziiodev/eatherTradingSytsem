"""End-to-end coverage for /api/accounts CRUD + nested pairs + tenancy.

Mirrors :mod:`aether_api.routers.accounts`:

* Auth + CSRF gating.
* Cross-tenant denial → 404 (account CRUD AND nested pair routes).
* MFA gate: non-MFA user creating a ``real`` account → 409.
* Account create rejects a foreign exchange_id → 404 (no leak).
* Nested pair create takes the account from the path (no credential fields).
* RESTRICT delete (account with a pair) → 409.
"""

from __future__ import annotations

import pytest

from aether_api.auth.cookies import CSRF_COOKIE

pytestmark = pytest.mark.integration


async def _seed_user_and_login(
    client,
    *,
    email: str = "acc-ops@example.com",
    password: str = "correct horse battery staple",
):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password=password)
        await session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return user


def _csrf(client) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE)
    assert token
    return {"X-CSRF-Token": token}


async def _create_exchange(client, *, code: str = "ICM") -> str:
    resp = await client.post(
        "/api/exchanges",
        json={"name": "IC Markets", "code": code, "kind": "broker"},
        headers=_csrf(client),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_account(client, exchange_id: str, **overrides) -> dict:
    body = {
        "exchange_id": exchange_id,
        "name": "main-account",
        "broker_name": "ICMarkets",
        "account_currency": "USD",
        "account_type": "demo",
    }
    body.update(overrides)
    resp = await client.post("/api/accounts", json=body, headers=_csrf(client))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _pair_payload(**overrides) -> dict:
    body = {
        "name": "Aether-EURUSD-H1",
        "symbol": "EURUSD",
        "timeframe": "H1",
        "mcp_url": "http://mcp.local:8081",
        "trading_sessions": ["europe"],
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Auth + CSRF
# ---------------------------------------------------------------------------
async def test_list_requires_auth(app_client):
    resp = await app_client.get("/api/accounts")
    assert resp.status_code == 401


async def test_create_without_csrf_is_403(app_client):
    await _seed_user_and_login(app_client)
    ex = await _create_exchange(app_client)
    resp = await app_client.post(
        "/api/accounts", json={"exchange_id": ex, "name": "a"}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CRUD happy paths
# ---------------------------------------------------------------------------
async def test_create_and_get(app_client):
    await _seed_user_and_login(app_client)
    ex = await _create_exchange(app_client)
    acc = await _create_account(app_client, ex)
    assert acc["exchange_id"] == ex
    assert acc["account_type"] == "demo"
    one = await app_client.get(f"/api/accounts/{acc['id']}")
    assert one.status_code == 200


async def test_list_filter_by_exchange(app_client):
    await _seed_user_and_login(app_client)
    ex1 = await _create_exchange(app_client, code="EX1")
    ex2 = await _create_exchange(app_client, code="EX2")
    await _create_account(app_client, ex1, name="a1")
    await _create_account(app_client, ex2, name="a2")
    resp = await app_client.get(f"/api/accounts?exchange_id={ex1}")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_create_with_foreign_exchange_is_404(app_client):
    # A creates an exchange; B tries to attach an account to it.
    await _seed_user_and_login(app_client, email="acc-fa@example.com")
    ex = await _create_exchange(app_client)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="acc-fb@example.com")
    resp = await app_client.post(
        "/api/accounts",
        json={"exchange_id": ex, "name": "x"},
        headers=_csrf(app_client),
    )
    assert resp.status_code == 404


async def test_patch_updates_fields(app_client):
    await _seed_user_and_login(app_client)
    ex = await _create_exchange(app_client)
    acc = await _create_account(app_client, ex)
    resp = await app_client.patch(
        f"/api/accounts/{acc['id']}",
        json={"broker_name": "NewBroker"},
        headers=_csrf(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["broker_name"] == "NewBroker"


# ---------------------------------------------------------------------------
# MFA gate
# ---------------------------------------------------------------------------
async def test_create_real_account_without_mfa_is_409(app_client):
    await _seed_user_and_login(app_client)
    ex = await _create_exchange(app_client)
    resp = await app_client.post(
        "/api/accounts",
        json={"exchange_id": ex, "name": "real-1", "account_type": "real"},
        headers=_csrf(app_client),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "MFA_REQUIRED_FOR_REAL_ACCOUNT"


# ---------------------------------------------------------------------------
# Nested pairs under an account
# ---------------------------------------------------------------------------
async def test_create_pair_under_account(app_client):
    await _seed_user_and_login(app_client)
    ex = await _create_exchange(app_client)
    acc = await _create_account(app_client, ex)
    resp = await app_client.post(
        f"/api/accounts/{acc['id']}/pairs",
        json=_pair_payload(),
        headers=_csrf(app_client),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["account_id"] == acc["id"]
    assert body["status"] == "inactive"


async def test_list_pairs_under_account(app_client):
    await _seed_user_and_login(app_client)
    ex = await _create_exchange(app_client)
    acc = await _create_account(app_client, ex)
    await app_client.post(
        f"/api/accounts/{acc['id']}/pairs",
        json=_pair_payload(name="P1"),
        headers=_csrf(app_client),
    )
    resp = await app_client.get(f"/api/accounts/{acc['id']}/pairs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_account_with_pair_cannot_be_deleted(app_client):
    await _seed_user_and_login(app_client)
    ex = await _create_exchange(app_client)
    acc = await _create_account(app_client, ex)
    await app_client.post(
        f"/api/accounts/{acc['id']}/pairs",
        json=_pair_payload(),
        headers=_csrf(app_client),
    )
    resp = await app_client.delete(
        f"/api/accounts/{acc['id']}", headers=_csrf(app_client)
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Cross-tenant — 404 on account AND nested pair routes
# ---------------------------------------------------------------------------
async def test_get_account_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="acc-a@example.com")
    ex = await _create_exchange(app_client)
    acc = await _create_account(app_client, ex)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="acc-b@example.com")
    resp = await app_client.get(f"/api/accounts/{acc['id']}")
    assert resp.status_code == 404


async def test_list_nested_pairs_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="acc-na@example.com")
    ex = await _create_exchange(app_client)
    acc = await _create_account(app_client, ex)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="acc-nb@example.com")
    resp = await app_client.get(f"/api/accounts/{acc['id']}/pairs")
    assert resp.status_code == 404


async def test_create_nested_pair_cross_tenant_returns_404(app_client):
    await _seed_user_and_login(app_client, email="acc-ca@example.com")
    ex = await _create_exchange(app_client)
    acc = await _create_account(app_client, ex)
    app_client.cookies.clear()
    await _seed_user_and_login(app_client, email="acc-cb@example.com")
    resp = await app_client.post(
        f"/api/accounts/{acc['id']}/pairs",
        json=_pair_payload(),
        headers=_csrf(app_client),
    )
    assert resp.status_code == 404
