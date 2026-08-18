"""Minimal RSA private key handling, so the client needs no third party packages.

Upstream stkclient depends on the ``rsa`` package for three things: parsing the PKCS#1 private
key amazon hands out at device registration, turning bytes into integers and back, and one
modular exponentiation. All three are a few lines of standard library, and dropping the
dependency is what lets this package be vendored into a calibre plugin unchanged.
"""

import base64
from typing import Tuple

KEY_SIZE_BYTES = 256  # amazon issues 2048 bit device keys


class KeyError_(ValueError):
    """Raised when a private key cannot be parsed."""


def load_pkcs1(pem: bytes) -> Tuple[int, int, int]:
    """Parses a PKCS#1 ``RSA PRIVATE KEY`` PEM into its modulus, exponents.

    Args:
        pem: The PEM encoded key, with or without the armor lines.

    Returns:
        A ``(n, e, d)`` tuple: modulus, public exponent, private exponent.

    Raises:
        KeyError_: The key is not a parseable PKCS#1 RSA private key.
    """
    der = _strip_armor(pem)
    body = _expect(der, 0, 0x30)  # RSAPrivateKey ::= SEQUENCE
    ints = []
    pos = body[0]
    end = body[1]
    while pos < end and len(ints) < 4:
        start, stop = _expect(der, pos, 0x02)  # INTEGER
        ints.append(int.from_bytes(der[start:stop], "big"))
        pos = stop
    if len(ints) < 4:
        raise KeyError_("Truncated RSA private key")
    version, n, e, d = ints
    if version != 0:
        raise KeyError_("Unsupported RSA private key version {}".format(version))
    return n, e, d


def sign(n: int, d: int, padded: bytes) -> bytes:
    """Applies the private key to an already padded block.

    Args:
        n: The key modulus.
        d: The private exponent.
        padded: The padded block to sign, at most ``n`` in value.

    Returns:
        The signature, left padded to the key size.
    """
    return pow(int.from_bytes(padded, "big"), d, n).to_bytes(KEY_SIZE_BYTES, "big")


def _strip_armor(pem: bytes) -> bytes:
    """Base64 decodes a PEM body, ignoring any ``-----BEGIN/END-----`` lines.

    Args:
        pem: The PEM encoded key.

    Returns:
        The DER bytes.

    Raises:
        KeyError_: The body is not valid base64.
    """
    lines = [l.strip() for l in pem.replace(b"\r", b"").split(b"\n")]
    body = b"".join(l for l in lines if l and not l.startswith(b"-----"))
    try:
        return base64.b64decode(body)
    except Exception as err:
        raise KeyError_("Private key is not valid base64: {}".format(err)) from err


def _expect(der: bytes, pos: int, tag: int) -> Tuple[int, int]:
    """Reads one DER element of the given tag, returning the bounds of its contents.

    Only the two types a PKCS#1 key is built from are supported: SEQUENCE and INTEGER.

    Args:
        der: The DER encoded key.
        pos: Offset of the element.
        tag: The tag byte the element must have.

    Returns:
        A ``(start, end)`` tuple delimiting the element's contents.

    Raises:
        KeyError_: The element is missing, truncated, or of the wrong type.
    """
    if pos >= len(der) or der[pos] != tag:
        raise KeyError_("Expected DER tag {:#x} at offset {}".format(tag, pos))
    pos += 1
    if pos >= len(der):
        raise KeyError_("Truncated DER length")
    length = der[pos]
    pos += 1
    if length & 0x80:  # long form: the low bits give the number of length bytes
        count = length & 0x7F
        if count == 0 or pos + count > len(der):
            raise KeyError_("Unsupported or truncated DER length")
        length = int.from_bytes(der[pos:pos + count], "big")
        pos += count
    end = pos + length
    if end > len(der):
        raise KeyError_("DER element runs past the end of the key")
    return pos, end
