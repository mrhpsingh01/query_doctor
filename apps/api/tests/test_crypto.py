import pytest
from cryptography.fernet import Fernet

from app.security.crypto import DecryptionError, decrypt, encrypt

KEY = Fernet.generate_key().decode()


def test_round_trip_and_ciphertext_differs():
    token = encrypt("s3cret-password", key=KEY)
    assert token != "s3cret-password"
    assert "s3cret" not in token
    assert decrypt(token, key=KEY) == "s3cret-password"


def test_same_plaintext_encrypts_differently_each_time():
    assert encrypt("x", key=KEY) != encrypt("x", key=KEY)


def test_tampered_ciphertext_is_rejected():
    token = encrypt("hello", key=KEY)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(DecryptionError):
        decrypt(tampered, key=KEY)


def test_wrong_key_is_rejected():
    token = encrypt("hello", key=KEY)
    with pytest.raises(DecryptionError):
        decrypt(token, key=Fernet.generate_key().decode())
