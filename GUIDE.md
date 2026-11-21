# Step-by-step guide: testing a `$lookup` use case

This guide walks through benchmarking one use case end to end (example: `popular_tag`),
then repeating for others. Everything is controlled by `benchmark.yaml`.

## 0. Prerequisites
- Atlas M40 cluster (MongoDB 9) and a database user with read/write access.
- Your IP in the Atlas network access list.
- A load-generator host in the **same region** as the cluster (a laptop adds network noise).
- Python 3.12.

## 1. Install
```bash
cd mongodb-locust
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -c "import pymongo; print(pymongo.version)"   # expect 4.20.1
```

## 2. Set the connection string
Create `.env` (never commit it):
```
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/
```
`benchmark.yaml` reads it through `uri: ${MONGODB_URI}`.

## 3. Review `benchmark.yaml`
| Setting | What to check |
|---|---|
| `atlas.database` | A dedicated benchmark DB (the tool drops its collections with `--drop`) |
| `data.customers` | 1,000,000 for the first run; raise it to test a larger working set |
| `scenario.active` | The use case to test (see step 7 for the list) |
| `scenario.lookup_style` | `simple` or `pipeline` join form |
| `load.users` / `load.duration_seconds` | Constant concurrent users and measured window |
| `load.spawn_rate` | Target number of users started per second during ramp-up |
| `thresholds` | Your pass/fail targets (placeholders: p95 <= 200 ms, errors <= 0.1%) |

### Automatic warm-up

Warm-up is calculated automatically as `ceil(users / spawn_rate) + 20` seconds and
includes ramp-up. The extra 20 seconds are intended for stabilization at the target
load. For example, 100 users at 10 users/second gives 30 seconds; 200 users gives
40 seconds. The calculation also applies when using `--users`.

This is an estimate, not a guarantee: startup and worker delays can extend ramp-up.
The runner warns if the reported user count is below the target when measurement starts;
in that case, the results include ramp-up. Check the actual user count and rerun after
resolving the delay. Remove `load.warmup_seconds` from older config files; it is no
longer a user setting.

For a quick dry run first, temporarily use `--customers 50000 --repeats 1` and shorter
`load.duration_seconds` (e.g. 60), then restore the real values.

## 4. Generate data
```bash
python bench.py generate --drop
```
- Creates `digital_customer` and `digital_customer_tag` from one seeded Faker source.
- Takes roughly a minute for 1M customers (about 5M tag docs).
- Without `--drop` it refuses to overwrite existing data that doesn't match the config,
  and skips if the data already matches.

## 5. Create indexes
```bash
python bench.py index
```
Builds the customer and tag indexes, including the unique `{customer_id, tag_code}` and the
`{tag_code, active, customer_id}` index that drives the tag-first join.

## 6. Verify the data
```bash
python bench.py verify
```
Expect all `OK` lines: fingerprint matches, customer count equals the config, the indexes
exist, and each tag's observed share is close to its expected share. It exits non-zero on
failure. Fix any `FAIL` before benchmarking.

## 7. Choose the use case
Set `scenario.active` in `benchmark.yaml`, or pass `--scenario NAME`.

| Scenario | Direction | What it tests |
|---|---|---|
| `popular_tag` | tag-first | A high-frequency tag such as `RENEWAL_DUE`, then join to customers |
| `rare_tag` | tag-first | A highly selective tag (`FRAUD_WATCH`) |
| `multi_tag_and` | tag-first | Customers having 2 tags (`$group` and `$lookup`) |
| `multi_tag_any` | tag-first | Customers having any of 3 tags |
| `tag_with_region` | tag-first | Tag match, join, then a customer attribute filter after the join |
| `tag_hydrated` | tag-first | Join to customers, then a second join back for the full tag list |
| `attr_then_lookup` | customer-first | Filter customers, then `$lookup` their tags |
| `not_tag` | customer-first | Customers without a given tag |
| `mixed` | blend | Weighted blend of the above (default, tag-first heavy) |

To add your own, add an entry under `scenario.definitions` in the YAML. For example:
```yaml
vip_in_region: {pipeline: tag_first, tag_pool: popular, n_tags: 1, customer_filter: [region]}
```
Valid fields: `pipeline` (`tag_first` or `customer_first`), `tag_pool` (`popular`, `mid` or
`rare`), `n_tags`, `mode` (`and` or `any`), `customer_filter` (`region`, `policy_line`),
`exclude_tag`, `hydrate_tags`, `lookup_style`.

## 8. Inspect the query plan (before loading the cluster)
```bash
python bench.py explain --scenario popular_tag
```
Check the printed values and the JSON in `results/explain/<scenario>.json`:
- `totalKeysExamined` and `totalDocsExamined` are small (tens to hundreds, not millions).
- The `$lookup` `strategy` shows an indexed lookup (`IndexedLoopJoin`) rather than a
  collection scan or hash join. A scan means a missing index, so go back to step 5.
- `executionTimeMillis` is in a sensible range for a single call.

## 9. Run the load test
```bash
python bench.py run --scenario popular_tag
```
What happens:
1. Locust starts with `load.workers` worker processes, all against the same config.
2. The automatically calculated warm-up stage is **excluded** from the results.
3. The measured stage then runs at a constant `load.users` (default 100) for `duration_seconds`
   (default 600). Its stats are saved to `results/<timestamp>_<scenario>_r<N>/steps.csv`.
   Users ramp up at `spawn_rate` per second (100 users at 10/s takes about 10 s), and the ramp falls
   inside the warm-up.
4. It repeats `load.repeats` times (default 3). With the default settings, one scenario
   takes about 3 x (0.5 + 10) = 31.5 minutes.

While it runs, watch in Atlas: CPU, disk IOPS, cache evictions, opcounters, and slow queries
in the Query Profiler. Also watch the load generator's own CPU; if it is saturated, your numbers
reflect the client, not the database.

## 10. Build the report
```bash
python bench.py report --scenario popular_tag
```
Output goes to `results/report_<scenario>/`:
- `report.md`: aggregate p50, p95 and p99, RPS and error rate at the configured user count (medians across repeats), p95 per query shape, and PASS/FAIL against `thresholds`.
- `latency.png`: p50/p95/p99 per query shape at the configured user count.
- `summary.csv`: the raw table.

## 11. Read the results
- **Healthy:** p95 grows slowly with users, error rate stays near 0, CPU stays under about 80%.
- **Finding capacity:** run the same scenario at several user counts (`--users 50`, `--users 100`, `--users 200`) and compare. The count where p95 rises sharply and RPS stops growing is the saturation point.
- **Compare join forms:** run again with `--scenario ...` after changing `scenario.lookup_style` to `pipeline`, and compare the two reports (they are stored under different run folders).
- **Slow shapes in `mixed`:** use the per-shape p95 table to find which scenario drags the blend, then run it on its own.

## 12. Repeat for other use cases
```bash
for s in popular_tag rare_tag multi_tag_and multi_tag_any tag_with_region tag_hydrated attr_then_lookup not_tag mixed; do
  python bench.py run --scenario $s && python bench.py report --scenario $s
done
```
The data and indexes are reused; only the query mix changes. Before a scenario sweep, make sure
`verify` still passes.

## 13. Reproducibility checklist
Each run folder holds `config.effective.yaml` (secrets are not written, only `${MONGODB_URI}`),
`meta.json` (server version, pymongo version, customer count) and `steps.csv`. To compare runs
fairly, keep these the same: cluster tier, data (same seed and customer count), user count and duration, client
host, and time of day if the cluster is shared.

## Troubleshooting
| Symptom | Likely cause and fix |
|---|---|
| `environment variable MONGODB_URI ... is not set` | `.env` missing, or run from another directory |
| `Collections are not empty and data does not match the config` | Config changed since loading; run `generate --drop` |
| `WARNING: loaded data does not match this config` on `run` | Same as above; results would not match the config |
| Connection or timeout errors | Atlas IP access list, or wrong URI |
| High error rate at high users | Raise `atlas.max_pool_size`, or the cluster is saturated |
| Flat RPS with low cluster CPU | Load generator is the bottleneck: add `load.workers` or a bigger host |
| `rare_tag` returns fewer than 20 rows | Expected; the tag applies to only about 500-800 customers |
