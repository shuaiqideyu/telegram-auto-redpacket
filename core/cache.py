"""Redis 缓存层：统一连接 + key 前缀。

本地 Redis，前缀 hongbao:；所有需要缓存的模块通过本模块读写。
"""
import os

import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/10")
KEY_PREFIX = "hongbao:"

_pool: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _pool


def key(suffix: str) -> str:
    return f"{KEY_PREFIX}{suffix}"


async def get(suffix: str) -> str | None:
    return await _get_redis().get(key(suffix))


async def set(suffix: str, value: str, ex: int | None = None):
    await _get_redis().set(key(suffix), value, ex=ex)


async def setnx(suffix: str, value: str, ex: int | None = None) -> bool:
    """原子 SET NX：键不存在才写入。返回 True 表示本次是首次写入（抢到）。"""
    return bool(await _get_redis().set(key(suffix), value, nx=True, ex=ex))


async def hget(suffix: str, field: str) -> str | None:
    return await _get_redis().hget(key(suffix), field)


async def hset(suffix: str, field: str, value: str):
    await _get_redis().hset(key(suffix), field, value)


async def hgetall(suffix: str) -> dict[str, str]:
    return await _get_redis().hgetall(key(suffix))


async def hmset(suffix: str, mapping: dict[str, str]):
    if mapping:
        await _get_redis().hset(key(suffix), mapping=mapping)


async def mget(suffixes: list[str]) -> list[str | None]:
    """批量 GET，返回与 suffixes 等长的列表。"""
    if not suffixes:
        return []
    keys = [key(s) for s in suffixes]
    return await _get_redis().mget(keys)


async def mset(mapping: dict[str, str]):
    """批量 SET。"""
    if not mapping:
        return
    kv = {key(s): v for s, v in mapping.items()}
    await _get_redis().mset(kv)


async def delete(suffix: str):
    await _get_redis().delete(key(suffix))


async def close():
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None
