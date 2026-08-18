"""Implements RSA request signing for amazon APIs."""

import base64
import datetime
import hashlib
from dataclasses import dataclass
from typing import Optional, Tuple

from . import _rsa
from .model import DeviceInfo


@dataclass(frozen=True, repr=False)
class Signer:
    """Implements RSA request signing for amazon APIs.

    Attributes:
        device_private_key: The (modulus, private exponent) of the key used to generate the
            X-ADP-Request-Digest header.
        adp_token: The value to be included in the X-ADP-Authentication-Token header.
    """

    device_private_key: Tuple[int, int]
    adp_token: str

    @staticmethod
    def from_device_info(d: DeviceInfo) -> "Signer":
        """Constructs a Signer instance from a DeviceInfo object."""
        n, unused_e, priv = _rsa.load_pkcs1(d.device_private_key.encode("utf-8"))
        return Signer(
            device_private_key=(n, priv),
            adp_token=d.adp_token,
        )

    def digest_header_for_request(
        self, method: str, path: str, post_data: str, signing_date: Optional[str] = None
    ) -> str:
        """Computes the request digest to be included in the X-ADP-Request-Digest header.

        Args:
            method: The HTTP method of the request
            path: The path portion of the URL being requested (starting with /).
            post_data: The HTTP request body if present, or an empty string
            signing_date: The date included in the signature. If null, the current date is used.

        Returns:
            The request digest to be included in the X-ADP-Request-Digest header.
        """
        if signing_date is None:
            signing_date = _get_signing_date()
        sig_data = self._make_digest_data_for_request(method, path, post_data, signing_date)
        digest = _sha256(sig_data)
        pad = bytes.fromhex(
            "01ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff00"
        )
        padded = pad + digest
        n, d = self.device_private_key
        encrypted_bytes = _rsa.sign(n, d, padded)
        bytes64 = base64.b64encode(encrypted_bytes).decode("utf-8")
        return f"{bytes64}:{signing_date}"

    def _make_digest_data_for_request(
        self, method: str, path: str, post_data: str, signing_date: Optional[str] = None
    ) -> bytes:
        if signing_date is None:
            signing_date = _get_signing_date()
        sig_data = "\n".join([method, path, signing_date, post_data, self.adp_token])
        return sig_data.encode("utf-8")


# 2021-10-09T05:02:38Z
def _get_signing_date() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256(s: bytes) -> bytes:
    m = hashlib.sha256()
    m.update(s)
    return m.digest()
