# RS256 Key Rotation Runbook

This runbook covers rotating the RS256 JWT signing keypair held by the
Aether API. It assumes the steady-state RS256 deployment (HS256 fallback
already disabled by `rs256-jwt-cleanup`, or in the transitional window
where HS256 verify-only is still enabled).

The golden rule is **publish-before-sign**: a new key MUST appear in JWKS
before any token is signed with it, so verifiers can pre-warm their cache.
Tokens signed by the outgoing key MUST keep verifying until the longest
plausibly-cached access token has expired (one TTL — currently 15 min).

---

## 0. Prerequisites

- Operator has shell access to the API host (or KMS console).
- The current public key set is published at `${API_BASE_URL}/.well-known/jwks.json`.
- A monitoring dashboard tracks 401 rates on `/api/me` and friends — a
  rotation that goes wrong shows up here within seconds.

---

## 1. Generate the new keypair

### Dev / local
```
cd apps/api
mv .dev_keys/private.pem .dev_keys/private.pem.old
mv .dev_keys/public.pem  .dev_keys/public.pem.old
uv run python scripts/gen_dev_keys.py
```

### Prod (KMS / Vault)
Create a new 2048-bit RSA key in the chosen backend. Export the PUBLIC
key only — the private key never leaves the KMS boundary. Keep the old
key fully accessible (signing-disabled but readable) until step 4.

The new `kid` is derived by `auth/keys.py:compute_kid` — the first 16
chars of the base64url-no-pad SHA-256 of the canonical JWK members
(`kty`, `n`, `e` — RFC 7638 thumbprint members). You can pre-compute it
locally before deploying:
```python
from aether_api.auth.keys import compute_kid, _load_public_pem
print(compute_kid(_load_public_pem(open("new_public.pem","rb").read())))
```

---

## 2. PUBLISH the new public key (signing still on the OLD key)

Modify the configuration so the API loads BOTH public keys but continues
signing with the old private key. In the current single-key implementation
this requires a small, temporary extension to `auth/keys.py` to load a
second public key and merge it into the JWK Set returned by
`auth/jwks.py`. The signing path remains pinned to the old `kid`.

Deploy. Verify:
```
curl ${API_BASE_URL}/.well-known/jwks.json | jq .keys
```
You MUST see two entries — old `kid` and new `kid`. The edge middleware
(`jose.createRemoteJWKSet`) will refresh its cache on the next request
that references the new `kid`; the old `kid` cache stays warm.

**Wait at least the full JWKS cache TTL (1 h by default — `Cache-Control:
max-age=3600`).** Verifiers fetched the JWKS before the publish step still
hold an old key set that lacks the new `kid`; they must roll forward
before any new-`kid` tokens hit them.

---

## 3. FLIP issuance to the new private key

Update the runtime configuration:
- Point `JWT_PRIVATE_KEY_PATH` / `JWT_PRIVATE_KEY_PEM` at the new private key.
- Leave `JWT_PUBLIC_KEY_*` pointing at the new public key.
- Keep the old public key in the JWK Set extension from step 2.

Restart the API. `issue_access_token` now stamps the new `kid` into every
token header. Tokens issued before this moment continue to verify because
the old public key is still published.

Sanity check:
```
# Fresh login — header.kid must be the new kid.
curl -c jar.txt -X POST -d '{"email":"...","password":"..."}' \
  ${API_BASE_URL}/api/auth/login
python -c "
import jwt, http.cookiejar
cj = http.cookiejar.MozillaCookieJar('jar.txt'); cj.load()
tok = next(c.value for c in cj if c.name == 'aether_access')
print(jwt.get_unverified_header(tok))
"
```

---

## 4. DRAIN the old key

Wait at least `ACCESS_TOKEN_TTL_MINUTES` (default 15 min). Any token signed
by the old key will have expired naturally — its bearer either refreshed
(and got a new-`kid` token in the response) or has been kicked back to
`/login`.

During drain, monitor 401 rate. A spike here means a verifier somewhere
cached the old JWKS, missed step 2's TTL, and is now rejecting new-`kid`
tokens. The fix is to clear the verifier's cache (or wait for it to
refresh — the edge middleware's `cacheMaxAge` is 1 h with a 6 h
stale-while-error window).

---

## 5. REMOVE the old key from the JWK Set

Roll back the step-2 configuration change so `auth/jwks.py` publishes ONLY
the active public key again. Deploy.

Verify:
```
curl ${API_BASE_URL}/.well-known/jwks.json | jq '.keys | length'
# → 1
```

The old private key can now be destroyed (KMS: schedule deletion, NOT
immediate — KMS deletion is irreversible and you want a 7-day grace
window for incident response).

---

## Quick reference

| Step | Old key signs | Old key verifies | New key signs | New key verifies |
|------|---------------|------------------|---------------|------------------|
| 0    | ✓             | ✓                | —             | —                |
| 2    | ✓             | ✓                | —             | ✓ (published)    |
| 3    | —             | ✓                | ✓             | ✓                |
| 4    | —             | ✓ (draining)     | ✓             | ✓                |
| 5    | —             | —                | ✓             | ✓                |

Never skip step 2's wait. Never delete the old private key before step 5
completes. Both shortcuts produce silent verification failures that look
exactly like user error to the dashboard.
