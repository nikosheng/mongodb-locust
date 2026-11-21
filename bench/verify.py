from bench import db
from bench.indexes import index_specs


def verify(cfg) -> bool:
    database = db.get_db(cfg)
    c, t = cfg.atlas.collections.customer, cfg.atlas.collections.tag
    ok = True

    meta = database["_bench_meta"].find_one({"_id": "fingerprint"})
    want = cfg.data.fingerprint()
    if not meta:
        print("FAIL no fingerprint: run `generate` first")
        return False
    match = meta["fingerprint"] == want
    ok &= match
    print(f"{'OK  ' if match else 'FAIL'} data fingerprint {meta['fingerprint']} (config {want})")

    nc, nt = database[c].count_documents({}), database[t].count_documents({})
    good = nc == cfg.data.customers
    ok &= good
    print(f"{'OK  ' if good else 'FAIL'} {c}: {nc:,} docs (config {cfg.data.customers:,})")
    print(f"INFO {t}: {nt:,} docs, avg {nt / max(nc, 1):.2f} tags/customer")

    have = {coll: {i["name"] for i in database[coll].list_indexes()} for coll in (c, t)}
    for coll, specs in index_specs(cfg).items():
        for _, opts in specs:
            present = opts["name"] in have[coll]
            ok &= present
            print(f"{'OK  ' if present else 'FAIL'} index {coll}.{opts['name']}")

    print("Tag distribution (observed vs expected share of customers):")
    counts = {d["_id"]: d["n"] for d in database[t].aggregate([{"$group": {"_id": "$tag_code", "n": {"$sum": 1}}}])}
    for tag in cfg.data.tag_catalog:
        obs = counts.get(tag.code, 0) / max(nc, 1)
        print(f"  {tag.code:<16} {obs:8.4f}  (expected {tag.freq:.4f})")
    return ok
