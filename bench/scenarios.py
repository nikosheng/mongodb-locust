"""Pipeline builders. Every scenario is a pure function of (cfg, scenario name, rng)."""
import random


def resolve_mix(cfg) -> list[tuple[str, int]]:
    d = cfg.scenario.definitions[cfg.scenario.active]
    if d.weights:
        return [(n, w) for n, w in d.weights.items() if w > 0]
    return [(cfg.scenario.active, 1)]


def target_collection(cfg, name: str) -> str:
    d = cfg.scenario.definitions[name]
    c = cfg.atlas.collections
    return c.tag if d.pipeline == "tag_first" else c.customer


def _lookup(cfg, sd, frm, local, foreign, as_, extra=None):
    style = sd.lookup_style or cfg.scenario.lookup_style
    if style == "simple" and not extra:
        return {"$lookup": {"from": frm, "localField": local, "foreignField": foreign, "as": as_}}
    match = {"$expr": {"$eq": ["$" + foreign, "$$k"]}}
    match.update(extra or {})
    return {"$lookup": {"from": frm, "let": {"k": "$" + local}, "pipeline": [{"$match": match}], "as": as_}}


def _customer_filter(cfg, sd, rng, prefix: str) -> dict:
    f = {}
    if "region" in sd.customer_filter:
        f[prefix + "region"] = rng.choice(cfg.data.regions)
    if "policy_line" in sd.customer_filter:
        f[prefix + "policies.line"] = rng.choice(list(cfg.data.policy_lines))
    return f


def _start(cfg, rng) -> dict:
    """Random resume point so repeated calls page through different customers instead of re-reading the same ones."""
    from bench.datagen.generate import customer_id
    return {"$gte": customer_id(rng.randrange(cfg.data.customers))}


def _tag_first(cfg, sd, rng):
    c = cfg.atlas.collections
    page = cfg.scenario.page_size
    pool = cfg.data.pool(sd.tag_pool)
    codes = rng.sample(pool, min(sd.n_tags, len(pool)))
    extra = {} if sd.tag_pool == "rare" else {"customer_id": _start(cfg, rng)}
    if len(codes) == 1:
        stages = [{"$match": {"tag_code": codes[0], "active": True, **extra}}]
        local = "customer_id"
    else:
        stages = [
            {"$match": {"tag_code": {"$in": codes}, "active": True, **extra}},
            {"$group": {"_id": "$customer_id", "n": {"$sum": 1}}},
        ]
        if sd.mode == "and":
            stages.append({"$match": {"n": {"$gte": len(codes)}}})
        local = "_id"
    post = _customer_filter(cfg, sd, rng, "customer.")
    if not post:
        stages.append({"$limit": page})
    stages += [
        _lookup(cfg, sd, c.customer, local, "customer_id", "customer"),
        {"$unwind": "$customer"},
    ]
    if post:
        stages += [{"$match": post}, {"$limit": page}]
    if sd.hydrate_tags:
        stages.append(_lookup(cfg, sd, c.tag, "customer.customer_id", "customer_id", "tags", {"active": True}))
    return stages


def _customer_first(cfg, sd, rng):
    c = cfg.atlas.collections
    page = cfg.scenario.page_size
    stages = [{"$match": {**_customer_filter(cfg, sd, rng, ""), "customer_id": _start(cfg, rng)}}]
    if sd.exclude_tag:
        code = rng.choice(cfg.data.pool(sd.tag_pool))
        stages += [
            _lookup(cfg, sd, c.tag, "customer_id", "customer_id", "tags", {"tag_code": code, "active": True}),
            {"$match": {"tags": {"$size": 0}}},
            {"$limit": page},
        ]
    else:
        stages += [{"$limit": page}, _lookup(cfg, sd, c.tag, "customer_id", "customer_id", "tags")]
    return stages


def build(cfg, name: str, rng: random.Random) -> list[dict]:
    sd = cfg.scenario.definitions[name]
    if sd.pipeline == "tag_first":
        return _tag_first(cfg, sd, rng)
    return _customer_first(cfg, sd, rng)
