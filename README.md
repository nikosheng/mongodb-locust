# MongoDB `$lookup` benchmark (Clientel Advanced Search / Blue tag design)

Benchmarks Advanced Search queries that join `digital_customer` and the standalone
`digital_customer_tag` collection with `$lookup`, using Locust and pymongo 4.20.1.
Everything is configured in **`benchmark.yaml`**. Secrets are referenced as `${MONGODB_URI}` and read from `.env`.

## Setup
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# .env must define MONGODB_URI
```

## Workflow
```bash
python bench.py generate --drop      # Faker data -> digital_customer + digital_customer_tag
python bench.py index                # create indexes
python bench.py verify               # counts, indexes, tag distribution
python bench.py explain              # explain executionStats for the active scenario
python bench.py run                  # Locust repeats, then results/report_<scenario>/report.md + chart
python bench.py report               # rebuild the report from saved results (optional)
python bench.py all                  # generate, index, verify, explain, run and report
```
Overrides without editing the file: `--scenario rare_tag`, `--customers 5000000`, `--repeats 1`.

## Scenarios (`scenario.definitions`)
Tag-first (default): `popular_tag`, `rare_tag`, `multi_tag_and`, `multi_tag_any`, `tag_with_region`, `tag_hydrated`
Customer-first: `attr_then_lookup`, `not_tag`
Blend: `mixed` (weights, tag-first dominant). Switch with `scenario.active`.
Add a use case by adding a definition in YAML (or a builder in `bench/scenarios.py`).
`scenario.lookup_style` selects `simple` (localField/foreignField) or `pipeline` (let + `$expr`).

## Notes
- Each scenario run is stand-alone; there is no cross-model comparison.
- Reports aggregate all saved runs for the selected scenario, including earlier invocations.
- Warm-up is automatically calculated as `ceil(users / spawn_rate) + 20` seconds and excluded from results. Results are measured at a constant `load.users`, then medians across repeats (`--users N` to override). Startup delays can exceed the estimate; check for a ramp-up warning.
- Run the load generator in the same region as the cluster, and watch its CPU.
- `generate` stores a data fingerprint; `run` warns if the data doesn't match the config.
- Pages start from a random `customer_id` so repeated calls don't hit the same cached docs.
