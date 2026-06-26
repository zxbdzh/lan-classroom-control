import pytest
import os
from common.crypto import AESCipher


class TestAESCipher:
    def test_encrypt_decrypt_normal(self):
        key = AESCipher.generate_key()
        cipher = AESCipher(key)
        plaintext = b"Hello, World! This is a test message."
        ciphertext = cipher.encrypt(plaintext)
        assert ciphertext != plaintext
        decrypted = cipher.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_encrypt_decrypt_empty(self):
        key = AESCipher.generate_key()
        cipher = AESCipher(key)
        plaintext = b""
        ciphertext = cipher.encrypt(plaintext)
        decrypted = cipher.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_encrypt_decrypt_chinese(self):
        key = AESCipher.generate_key()
        cipher = AESCipher(key)
        plaintext = "测试中文加密解密".encode("utf-8")
        ciphertext = cipher.encrypt(plaintext)
        decrypted = cipher.decrypt(ciphertext)
        assert decrypted == plaintext
        assert decrypted.decode("utf-8") == "测试中文加密解密"

    def test_encrypt_different_each_time(self):
        key = AESCipher.generate_key()
        cipher = AESCipher(key)
        plaintext = b"same plaintext"
        ct1 = cipher.encrypt(plaintext)
        ct2 = cipher.encrypt(plaintext)
        assert ct1 != ct2
        assert cipher.decrypt(ct1) == plaintext
        assert cipher.decrypt(ct2) == plaintext

    def test_wrong_key(self):
        key1 = AESCipher.generate_key()
        key2 = AESCipher.generate_key()
        cipher1 = AESCipher(key1)
        cipher2 = AESCipher(key2)
        plaintext = b"secret data"
        ciphertext = cipher1.encrypt(plaintext)
        with pytest.raises(Exception):
            cipher2.decrypt(ciphertext)

    def test_from_password(self):
        password = "mysecretpassword123"
        cipher1 = AESCipher.from_password(password)
        cipher2 = AESCipher.from_password(password)
        plaintext = b"test password encryption"
        ciphertext = cipher1.encrypt(plaintext)
        decrypted = cipher2.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_from_password_deterministic(self):
        password = "consistent password"
        key1 = AESCipher.from_password(password).key
        key2 = AESCipher.from_password(password).key
        assert key1 == key2

    def test_generate_key_length(self):
        key = AESCipher.generate_key()
        assert len(key) == 32

    def test_invalid_key_length(self):
        with pytest.raises(ValueError):
            AESCipher(b"short")
        with pytest.raises(ValueError):
            AESCipher(b"a" * 20)
        AESCipher(b"a" * 16)
        AESCipher(b"a" * 24)
        AESCipher(b"a" * 32)

    def test_decrypt_too_short(self):
        key = AESCipher.generate_key()
        cipher = AESCipher(key)
        with pytest.raises(ValueError):
            cipher.decrypt(b"short")

    def test_large_data(self):
        key = AESCipher.generate_key()
        cipher = AESCipher(key)
        data = os.urandom(1024 * 1024)
        ciphertext = cipher.encrypt(data)
        decrypted = cipher.decrypt(ciphertext)
        assert decrypted == data

    def test_encrypt_large_decrypt_large(self):
        key = AESCipher.generate_key()
        cipher = AESCipher(key)
        data = os.urandom(2 * 1024 * 1024)
        ciphertext = cipher.encrypt_large(data, chunk_size=65536)
        decrypted = cipher.decrypt_large(ciphertext, chunk_size=65536)
        assert decrypted == data
