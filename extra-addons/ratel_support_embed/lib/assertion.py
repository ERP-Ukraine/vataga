"""Підтвердження особи для сервера Ratel Support (ТЗ v3.2 § Ч, ODOO-07).

Чистий код без залежності від Odoo: його ж перевіряє тест сервера Ratel
(`tests/test_odoo_module.py`) — формат мусить збігатися з `domain/odoo_identity.py`.

Формат: `<payload>.<signature>`, обидві частини — base64url без `=`. Підпис Ed25519 — над
`PREFIX + payload`. Підтвердження каже лише «хто це» й саме доступу не дає: сервер Ratel ще
вимагає підпис браузера, підтвердженого адміністратором Ratel.
"""

import base64
import json
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PREFIX = b"ratel-odoo-identity:v1:"
LIFETIME = 30  # секунд; сервер приймає не більше 60


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _key(private_key):
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key, validate=True))


def new_private_key():
    """Новий закритий ключ: 32 байти в base64."""
    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    return base64.b64encode(raw).decode()


def public_key(private_key):
    """Відкритий ключ (base64) — його вносять у налаштування сервера Ratel."""
    raw = (
        _key(private_key)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    return base64.b64encode(raw).decode()


def make_assertion(private_key, issuer, audience, uid, now, lifetime=LIFETIME):
    """Одноразове підтвердження для користувача `uid`; `now` — секунди UNIX."""
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": int(uid),
        "iat": int(now),
        "exp": int(now) + int(lifetime),
        "jti": secrets.token_urlsafe(24),
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    return body + "." + _b64url(_key(private_key).sign(PREFIX + body.encode()))
