"""
Access-code and user-password hashing. Uses stdlib PBKDF2 (no extra
dependency) rather than storing either in plaintext. Same algorithm serves
both secrets — an access code and a password are both just "a secret string
we need to verify without storing it" — so hash_password/verify_password are
aliases rather than a separate implementation.
"""
import hashlib
import hmac
import os


def hash_access_code(code: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 100_000)
    return f"{salt.hex()}:{dk.hex()}"


def verify_access_code(code: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(dk_hex)
    dk = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 100_000)
    return hmac.compare_digest(dk, expected)


hash_password = hash_access_code
verify_password = verify_access_code
