"""Pro licence: offline-verifiable Ed25519 keys, free-tier limits.

A licence key looks like ``te1.<payload>.<signature>`` (both base64url). The payload is a
small JSON object signed by the issuer (the checkout webhook) with an Ed25519 key; only
the public half lives here, so verification needs no network and the footage never has
to leave the machine. Ed25519 verification is implemented in pure Python (RFC 8032) to
keep Pillow the only runtime dependency.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FREE_MAX_RALLIES",
    "PRICING_URL",
    "WATERMARK_TEXT",
    "License",
    "LicenseError",
    "parse_key",
    "current",
    "is_pro",
    "activate",
    "deactivate",
    "license_path",
]

PUBLIC_KEY_HEX = "cdd3eeac6326bc9d571247ed8987de0f3b6b650213dada3d3ab0f7c98f167bf4"
FREE_MAX_RALLIES = 5
PRICING_URL = "https://tennis-evolution.pages.dev/#pricing"
WATERMARK_TEXT = "tennis-evolution · free"
KEY_PREFIX = "te1"


class LicenseError(ValueError):
    """The key is malformed or its signature does not match."""


@dataclass(frozen=True)
class License:
    key: str
    license_id: str
    plan: str
    source: str
    issued_at: int
    name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan,
            "license_id": self.license_id,
            "source": self.source,
            "issued_at": self.issued_at,
            "name": self.name,
        }


_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _add(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    pa = (y1 - x1) * (y2 - x2) % _P
    pb = (y1 + x1) * (y2 + x2) % _P
    pc = t1 * 2 * _D * t2 % _P
    pd = z1 * 2 * z2 % _P
    e, f, g, h = pb - pa, pd - pc, pd + pc, pb + pa
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    result = (0, 1, 1, 0)
    while s:
        if s & 1:
            result = _add(result, point)
        point = _add(point, point)
        s >>= 1
    return result


def _equal(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(data: bytes) -> tuple[int, int, int, int] | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_GY = 4 * _inv(5) % _P
_G = (_recover_x(_GY, 0) or 0, _GY, 1, (_recover_x(_GY, 0) or 0) * _GY % _P)


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 Ed25519 signature check."""
    if len(signature) != 64:
        return False
    point_a = _decompress(public_key)
    point_r = _decompress(signature[:32])
    if point_a is None or point_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    digest = hashlib.sha512(signature[:32] + public_key + message).digest()
    h = int.from_bytes(digest, "little") % _L
    return _equal(_mul(s, _G), _add(point_r, _mul(h, point_a)))


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse_key(key: str, *, public_key_hex: str | None = None) -> License:
    """Validate ``key`` and return its contents; raises :class:`LicenseError`."""
    text = "".join(key.split())
    parts = text.split(".")
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        raise LicenseError("not a tennis-evolution licence key (expected te1.<payload>.<sig>)")
    try:
        payload_raw = _b64decode(parts[1])
        signature = _b64decode(parts[2])
    except (ValueError, TypeError) as exc:
        raise LicenseError("licence key is not valid base64url") from exc
    message = f"{parts[0]}.{parts[1]}".encode()
    public = bytes.fromhex(public_key_hex or PUBLIC_KEY_HEX)
    if not ed25519_verify(public, message, signature):
        raise LicenseError("licence signature does not match; copy the whole key again")
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        raise LicenseError("licence payload is not JSON") from exc
    if payload.get("v") != 1 or payload.get("plan") != "pro":
        raise LicenseError("unsupported licence version or plan")
    return License(
        key=text,
        license_id=str(payload.get("lid", "")),
        plan="pro",
        source=str(payload.get("src", "")),
        issued_at=int(payload.get("iat", 0)),
        name=payload.get("name"),
    )


def license_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tennis-evolution" / "license"


def current() -> License | None:
    """The active licence from ``$TENNIS_EVOLUTION_LICENSE`` or the config file."""
    key = os.environ.get("TENNIS_EVOLUTION_LICENSE", "").strip()
    if not key:
        path = license_path()
        if not path.exists():
            return None
        key = path.read_text(encoding="utf-8").strip()
    if not key:
        return None
    try:
        return parse_key(key)
    except LicenseError:
        return None


def is_pro() -> bool:
    return current() is not None


def activate(key: str) -> License:
    """Verify ``key`` and store it for later runs."""
    lic = parse_key(key)
    path = license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lic.key + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return lic


def deactivate() -> bool:
    path = license_path()
    if path.exists():
        path.unlink()
        return True
    return False
