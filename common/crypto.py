import hashlib
import os
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes


class AESCipher:
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError(f"Key must be 16, 24, or 32 bytes, got {len(key)}")
        self.key = key

    @classmethod
    def from_password(cls, password: str) -> "AESCipher":
        key = hashlib.sha256(password.encode("utf-8")).digest()
        return cls(key)

    @classmethod
    def generate_key(cls) -> bytes:
        return get_random_bytes(32)

    def encrypt(self, plaintext: bytes) -> bytes:
        iv = get_random_bytes(16)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
        return iv + ciphertext

    def decrypt(self, ciphertext: bytes) -> bytes:
        if len(ciphertext) < 16:
            raise ValueError("Ciphertext too short")
        iv = ciphertext[:16]
        data = ciphertext[16:]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(data), AES.block_size)

    def encrypt_large(self, plaintext: bytes, chunk_size: int = 64 * 1024) -> bytes:
        iv = get_random_bytes(16)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        result = iv
        for i in range(0, len(plaintext), chunk_size):
            chunk = plaintext[i:i + chunk_size]
            if i + chunk_size >= len(plaintext):
                chunk = pad(chunk, AES.block_size)
            result += cipher.encrypt(chunk)
        return result

    def decrypt_large(self, ciphertext: bytes, chunk_size: int = 64 * 1024) -> bytes:
        if len(ciphertext) < 16:
            raise ValueError("Ciphertext too short")
        iv = ciphertext[:16]
        data = ciphertext[16:]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        result = b""
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            result += cipher.decrypt(chunk)
        return unpad(result, AES.block_size)
