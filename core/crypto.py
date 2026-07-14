"""Session 对称加密：AES-256-GCM。

密钥来自 SESSION_ENCRYPT_KEY（64 位 hex 或 base64）。
未配置密钥时透传明文（开发模式）。
密文格式：``ENC:`` + base64(nonce[12] + ciphertext + tag[16])。
未加密的 session 不以 ``ENC:`` 开头，解密时原样返回（向后兼容旧明文数据）。
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "ENC:"
_NONCE_LEN = 12
_KEY_LEN = 32


def _get_key() -> bytes | None:
    from .config import config
    raw = config.session_encrypt_key
    if not raw:
        return None
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        key = base64.b64decode(raw)
    if len(key) != _KEY_LEN:
        raise ValueError(
            f"SESSION_ENCRYPT_KEY 必须是 {_KEY_LEN} 字节"
            f"（64 位 hex 或等长 base64），当前 {len(key)} 字节")
    return key


def encrypt_session(plain: str) -> str:
    """加密 session 字符串。key 未配置时原样返回。"""
    key = _get_key()
    if key is None:
        return plain
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plain.encode(), None)
    return PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt_session(cipher: str | None) -> str | None:
    """解密 session 字符串。不以 ``ENC:`` 开头的原样返回（兼容旧明文）。"""
    if not cipher:
        return cipher
    if not cipher.startswith(PREFIX):
        return cipher
    key = _get_key()
    if key is None:
        raise ValueError("数据库中存在加密 session，但 SESSION_ENCRYPT_KEY 未配置")
    raw = base64.b64decode(cipher[len(PREFIX):])
    nonce, sealed = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, sealed, None).decode()
