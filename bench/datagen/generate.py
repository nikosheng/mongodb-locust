import random
import time
from datetime import datetime, time as dtime, timedelta
from multiprocessing import Pool

from faker import Faker
from pymongo import ASCENDING

from bench import db

POLICY_PREFIX = {"auto": "AU", "home": "HM", "life": "LF", "health": "HL"}
POLICY_STATUS = (["active", "lapsed", "cancelled"], [0.85, 0.10, 0.05])
KYC = (["verified", "pending", "expired"], [0.88, 0.08, 0.04])
SEGMENTS = (["mass", "affluent", "high_net_worth"], [0.70, 0.25, 0.05])
TAG_SOURCES = ["rule_engine", "agent", "campaign", "import"]
COLORS = ["Red", "Green", "Orange", "Purple", "Grey", "Yellow"]
META = "_bench_meta"


def customer_id(i: int) -> str:
    return f"CUST-{i:09d}"


def agent_id(i: int) -> str:
    return f"AGT-{i:05d}"


def _customer(fake: Faker, rng: random.Random, cfg, i: int) -> dict:
    d = cfg.data
    lines, lw = list(d.policy_lines), list(d.policy_lines.values())
    policies = []
    for _ in range(rng.choices([1, 2, 3, 4], [0.5, 0.3, 0.15, 0.05])[0]):
        line = rng.choices(lines, lw)[0]
        start = fake.date_time_between(start_date="-8y", end_date="now")
        policies.append({
            "policy_no": f"{POLICY_PREFIX.get(line, 'XX')}-{rng.randint(10**7, 10**8 - 1)}",
            "line": line,
            "status": rng.choices(*POLICY_STATUS)[0],
            "premium": round(rng.uniform(200, 6000), 2),
            "start_date": start,
            "end_date": start + timedelta(days=365),
        })
    first, last = fake.first_name(), fake.last_name()
    return {
        "customer_id": customer_id(i),
        "name": f"{first} {last}",
        "email": f"{first}.{last}.{i}@{fake.free_email_domain()}".lower(),
        "phone": fake.phone_number(),
        "dob": datetime.combine(fake.date_of_birth(minimum_age=18, maximum_age=90), dtime()),
        "region": rng.choice(d.regions),
        "agent_id": agent_id(rng.randrange(d.agents)),
        "kyc_status": rng.choices(*KYC)[0],
        "segment": rng.choices(*SEGMENTS)[0],
        "address": {
            "street": fake.street_address(),
            "city": fake.city(),
            "postcode": fake.postcode(),
        },
        "policies": policies,
        "created_at": fake.date_time_between(start_date="-10y", end_date="now"),
    }


def _tags(fake: Faker, rng: random.Random, cfg, cid: str) -> list[dict]:
    out = []
    for t in cfg.data.tag_catalog:
        if rng.random() < t.freq:
            created = fake.date_time_between(start_date="-3y", end_date="now")
            out.append({
                "customer_id": cid,
                "tag_code": t.code,
                "tag_name": t.code.replace("_", " ").title(),
                "category": t.category,
                "color": "Blue" if t.code == "BLUE" else rng.choice(COLORS),
                "active": rng.random() < 0.95,
                "source": rng.choice(TAG_SOURCES),
                "created_by": agent_id(rng.randrange(cfg.data.agents)),
                "created_at": created,
                "expires_at": created + timedelta(days=rng.randint(90, 730)) if rng.random() < 0.3 else None,
            })
    rng.shuffle(out)
    return out[: cfg.data.max_tags_per_customer]


def _shard(args):
    cfg, shard_no, start, end = args
    db.reset_client()
    database = db.get_db(cfg)
    cust, tag = database[cfg.atlas.collections.customer], database[cfg.atlas.collections.tag]
    fake = Faker(cfg.data.locale)
    fake.seed_instance(cfg.data.seed + shard_no)
    rng = random.Random(cfg.data.seed * 1_000_003 + shard_no)
    cbuf, tbuf, ntags = [], [], 0
    bs = cfg.data.batch_size
    for i in range(start, end):
        c = _customer(fake, rng, cfg, i)
        cbuf.append(c)
        t = _tags(fake, rng, cfg, c["customer_id"])
        tbuf.extend(t)
        ntags += len(t)
        if len(cbuf) >= bs:
            cust.insert_many(cbuf, ordered=False)
            cbuf = []
        if len(tbuf) >= bs:
            tag.insert_many(tbuf, ordered=False)
            tbuf = []
    if cbuf:
        cust.insert_many(cbuf, ordered=False)
    if tbuf:
        tag.insert_many(tbuf, ordered=False)
    return end - start, ntags


def generate(cfg, drop: bool = False):
    database = db.get_db(cfg)
    cust_name, tag_name = cfg.atlas.collections.customer, cfg.atlas.collections.tag
    fp = cfg.data.fingerprint()
    meta = database[META].find_one({"_id": "fingerprint"})
    populated = database[cust_name].estimated_document_count() > 0

    if populated and not drop:
        if meta and meta.get("fingerprint") == fp:
            print(f"Data already loaded and matches config (fingerprint {fp}); skipping. Use --drop to rebuild.")
            return
        raise SystemExit("Collections are not empty and data does not match the config. Re-run with --drop.")
    if drop:
        database[cust_name].drop()
        database[tag_name].drop()
        database[META].drop()

    n = cfg.data.customers
    size = cfg.data.shard_size
    tasks = [(cfg, k, s, min(s + size, n)) for k, s in enumerate(range(0, n, size))]
    print(f"Generating {n:,} customers in {len(tasks)} shards with {cfg.data.workers} workers...")
    t0, done, tags = time.time(), 0, 0
    with Pool(cfg.data.workers) as pool:
        for c, t in pool.imap_unordered(_shard, tasks):
            done += c
            tags += t
            rate = done / (time.time() - t0)
            print(f"  {done:,}/{n:,} customers, {tags:,} tags ({rate:,.0f} cust/s)", flush=True)
    database[META].replace_one(
        {"_id": "fingerprint"},
        {"_id": "fingerprint", "fingerprint": fp, "customers": n, "tags": tags, "at": datetime.utcnow()},
        upsert=True,
    )
    print(f"Done in {time.time() - t0:.0f}s. Run `bench.py index` next.")
