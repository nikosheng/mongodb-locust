import certifi
from pymongo import MongoClient

_client = None


def get_client(cfg) -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            cfg.atlas.uri,
            tlsCAFile=certifi.where(),
            maxPoolSize=cfg.atlas.max_pool_size,
            readPreference=cfg.atlas.read_preference,
            readConcernLevel=cfg.atlas.read_concern,
            appname="join-bench",
        )
    return _client


def get_db(cfg):
    return get_client(cfg)[cfg.atlas.database]


def reset_client():
    """Drop the cached client (needed after fork in multiprocessing workers)."""
    global _client
    _client = None
