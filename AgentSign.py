"""Signatures: an agent's identity is its signing key, not its address.

Every message an agent sends is signed with Ed25519. The receiver checks the
signature, so it knows the message really comes from that signer, and that
the same signer is behind different addresses or deals.

Hosted v1: the signing key is derived from the wallet key, so the server does
not store it:  seed = SHA-256("I Am Wallet sign v1|" + wallet key).
An agent can compute the same seed itself (v2: sign on the agent's side).

Uses the `cryptography` package when it is installed; otherwise a small
pure-Python Ed25519 (RFC 8032), slower but with no dependency.
"""
import hashlib

SEED_PREFIX = b"I Am Wallet sign v1|"


def seed_for_key(wallet_key):
    return hashlib.sha256(SEED_PREFIX + wallet_key.encode()).digest()


# ---------------- pure-Python Ed25519 (RFC 8032) ----------------

_p = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _p - 2, _p) % _p
_I = pow(2, (_p - 1) // 4, _p)


def _sha512(b):
    return hashlib.sha512(b).digest()


def _inv(x):
    return pow(x, _p - 2, _p)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (_p + 3) // 8, _p)
    if (x * x - xx) % _p != 0:
        x = (x * _I) % _p
    if x % 2 != 0:
        x = _p - x
    return x


_By = 4 * _inv(5) % _p
_B = (_xrecover(_By), _By, 1, _xrecover(_By) * _By % _p)   # extended coordinates


def _add(P, Q):
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % _p
    b = (y1 + x1) * (y2 + x2) % _p
    c = 2 * t1 * t2 * _d % _p
    dd = 2 * z1 * z2 % _p
    e, f, g, hh = b - a, dd - c, dd + c, b + a
    return (e * f % _p, g * hh % _p, f * g % _p, e * hh % _p)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _encode(P):
    x, y, z, _ = P
    zi = _inv(z)
    x, y = x * zi % _p, y * zi % _p
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode(b):
    if len(b) != 32:
        raise ValueError("bad point")
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _p:
        raise ValueError("bad point")
    x = _xrecover(y)
    if (x & 1) != (b[31] >> 7):
        x = _p - x
    P = (x, y, 1, x * y % _p)
    # on-curve check: -x^2 + y^2 = 1 + d x^2 y^2
    if (-x * x + y * y - 1 - _d * x * x * y * y) % _p != 0:
        raise ValueError("not on curve")
    return P


def _expand(seed):
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _py_public(seed):
    a, _ = _expand(seed)
    return _encode(_mul(a, _B))


def _py_sign(seed, msg):
    a, prefix = _expand(seed)
    A = _encode(_mul(a, _B))
    r = int.from_bytes(_sha512(prefix + msg), "little") % _L
    R = _encode(_mul(r, _B))
    k = int.from_bytes(_sha512(R + A + msg), "little") % _L
    S = (r + k * a) % _L
    return R + S.to_bytes(32, "little")


def _py_verify(pub, msg, sig):
    try:
        if len(sig) != 64:
            return False
        A = _decode(pub)
        R = _decode(sig[:32])
        S = int.from_bytes(sig[32:], "little")
        if S >= _L:
            return False
        k = int.from_bytes(_sha512(sig[:32] + pub + msg), "little") % _L
        left = _encode(_mul(S, _B))
        right = _encode(_add(R, _mul(k, A)))
        return left == right
    except (ValueError, TypeError):
        return False


# ---------------- public API (fast library when available) ----------------

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature

    def public_key(seed):
        return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def sign(seed, msg):
        return Ed25519PrivateKey.from_private_bytes(seed).sign(msg)

    def verify(pub, msg, sig):
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
            return True
        except (InvalidSignature, ValueError):
            return False

    ENGINE = "cryptography"
except ImportError:                      # no library: pure Python
    public_key, sign, verify = _py_public, _py_sign, _py_verify
    ENGINE = "pure-python"


def signer_id(pub):
    """Short public identity of a signer, shown to other agents."""
    return "awid_" + pub.hex()
