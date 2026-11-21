from pymongo import ASCENDING

from bench import db


def index_specs(cfg):
    c, t = cfg.atlas.collections.customer, cfg.atlas.collections.tag
    return {
        c: [
            ({"customer_id": 1}, {"unique": True, "name": "customer_id_1"}),
            ({"region": 1, "agent_id": 1}, {"name": "region_1_agent_id_1"}),
            ({"policies.line": 1}, {"name": "policies_line_1"}),
        ],
        t: [
            ({"customer_id": 1, "tag_code": 1}, {"unique": True, "name": "customer_id_1_tag_code_1"}),
            ({"tag_code": 1, "active": 1, "customer_id": 1}, {"name": "tag_code_1_active_1_customer_id_1"}),
        ],
    }


def create_indexes(cfg):
    database = db.get_db(cfg)
    for coll, specs in index_specs(cfg).items():
        for keys, opts in specs:
            print(f"  {coll}: creating {opts['name']} ...", flush=True)
            database[coll].create_index(list(keys.items()), **opts)
    print("Indexes ready.")
