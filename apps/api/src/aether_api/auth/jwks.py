"""JWKS endpoint — publishes the active RS256 public key for verifiers.

Verifiers (the Next.js edge middleware via ``jose.createRemoteJWKSet``,
future sister services) fetch this URL on a cache schedule, pick the key
whose ``kid`` matches the JWT header, and verify the signature locally.

The endpoint:

* Returns a JWK Set (RFC 7517 §5) with exactly the configured public key.
* During rotation a second key would appear here BEFORE issuance flips —
  publish-before-sign (see ``KEY_ROTATION.md``). This v1 publishes one key;
  the rotation flow is documented but not yet automated.
* Sets ``Cache-Control: public, max-age=3600`` — verifiers SHOULD cache for
  an hour. The matching ``jose.createRemoteJWKSet`` cooldown is 1h with a
  6h stale-while-error window on the edge.

Nothing here is gated by auth — the JWK Set contains only public material
and is intentionally world-readable.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Response

from aether_api.auth.keys import get_rsa_keypair, public_jwk

#: Mounted at the well-known well-known prefix per RFC 8615. No ``/api``
#: prefix — the edge middleware lives at the same origin during dev and
#: external verifiers expect the canonical location.
router = APIRouter(tags=["meta"])


@router.get(
    "/.well-known/jwks.json",
    summary="JSON Web Key Set for RS256 access-token verification",
    response_model=None,
)
def jwks() -> Response:
    """Return the JWK Set wrapping the active RS256 public key.

    Response shape::

        {
          "keys": [
            {"kty": "RSA", "use": "sig", "alg": "RS256",
             "kid": "<derived>", "n": "<b64url>", "e": "<b64url>"}
          ]
        }
    """
    keypair = get_rsa_keypair()
    body = {"keys": [public_jwk(keypair.public_key, kid=keypair.kid)]}
    return Response(
        content=_json_dumps(body),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _json_dumps(payload: Mapping[str, object]) -> str:
    """Compact JSON, stable key order, no trailing whitespace.

    Importing here (rather than top-level) keeps the module's import surface
    minimal and matches the rest of the codebase's style.
    """
    import json

    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
