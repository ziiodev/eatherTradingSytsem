"""Generate a dev-only RSA 2048-bit keypair for RS256 JWT signing.

USAGE:
    cd apps/api && uv run python scripts/gen_dev_keys.py

OUTPUT:
    apps/api/.dev_keys/private.pem   (PKCS#8, unencrypted, RSA 2048-bit)
    apps/api/.dev_keys/public.pem    (SubjectPublicKeyInfo)

The ``.dev_keys/`` directory is gitignored. NEVER commit private keys —
the script itself is committed; the PEM files it writes are not.

After running this script, point your local ``.env`` at the generated keys::

    JWT_ALGORITHM=RS256
    JWT_PRIVATE_KEY_PATH=.dev_keys/private.pem
    JWT_PUBLIC_KEY_PATH=.dev_keys/public.pem

For production, use a KMS-backed flow or supply ``JWT_PRIVATE_KEY_PEM`` /
``JWT_PUBLIC_KEY_PEM`` as multi-line env vars. See ``KEY_ROTATION.md``.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

#: Resolve ``apps/api/.dev_keys/`` relative to this script (scripts/ is one
#: level below the apps/api root).
KEYS_DIR: Path = Path(__file__).resolve().parent.parent / ".dev_keys"
PRIVATE_KEY_PATH: Path = KEYS_DIR / "private.pem"
PUBLIC_KEY_PATH: Path = KEYS_DIR / "public.pem"


def main() -> int:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)

    if PRIVATE_KEY_PATH.exists() or PUBLIC_KEY_PATH.exists():
        print(
            f"refusing to overwrite existing keys in {KEYS_DIR} — "
            "delete the files manually if you really want to regenerate.",
            file=sys.stderr,
        )
        return 1

    # 2048-bit RSA matches the design decision (#1989) — the JWT/JWS RS256
    # spec floor with broad library support. Public exponent 65537 is the
    # universal default.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    PRIVATE_KEY_PATH.write_bytes(private_pem)
    PUBLIC_KEY_PATH.write_bytes(public_pem)

    # Lock down the private key — best-effort chmod, not portable to Windows
    # but harmless if it fails.
    with contextlib.suppress(OSError):
        PRIVATE_KEY_PATH.chmod(0o600)

    print(f"wrote {PRIVATE_KEY_PATH}")
    print(f"wrote {PUBLIC_KEY_PATH}")
    print()
    print("Add to your apps/api/.env:")
    print("  JWT_ALGORITHM=RS256")
    print(f"  JWT_PRIVATE_KEY_PATH={PRIVATE_KEY_PATH.relative_to(KEYS_DIR.parent)}")
    print(f"  JWT_PUBLIC_KEY_PATH={PUBLIC_KEY_PATH.relative_to(KEYS_DIR.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
