# SQLite aggregate integration

`sqlite-aggregate-v1` executes real SQL in the independently maintained SQLite database engine. Jitter owns the read-only service driver, generated table and workload. This is a controlled in-memory database workload, not a measurement of a separately maintained production service, storage device, network database, transaction workload or SQLite CLI process.

## Pinned source and build

The unmodified SQLite 3.46.1 amalgamation and header are in `third_party/sqlite/`. Source ID is `2024-08-13 09:16:08 c9c2ab54ba1f5f46360f1b4f35d849cd3f080e6fc2b6c60e91b16c63f69a1e33`. The source was checked against the [upstream release's published SHA3-256](https://www.sqlite.org/releaselog/3_46_1.html): `186a1baa476b6d546de155160ca6d30ff7b7e6ee375f0bb6445e1a3d180a7dad`.

`provenance.json` records the official archive URL and SHA256 of both delivered files. CMake checks those hashes before compiling the static library. SQLite's [public-domain dedication](https://www.sqlite.org/copyright.html) is retained in the source and notice. This is a pinned research dependency, not a claim to use the latest release.

The library is compiled as C99 with `SQLITE_THREADSAFE=1`, `SQLITE_OMIT_LOAD_EXTENSION=1`, and `SQLITE_DQS=0`. No system SQLite binary or library is used. C and C++ flags are recorded in build logs; sanitizer builds instrument both languages.

## Data and query

Application fields are the common worker/queue/key/seed settings, `light_rows`, `heavy_rows`, and implementation `scan` or `indexed`. Both row counts are 1–4096. `key_count * max(light_rows, heavy_rows)` must not exceed one million prepared rows.

Before arrivals, create an in-memory table:

```sql
CREATE TABLE items(bucket INTEGER NOT NULL, seq INTEGER NOT NULL, value INTEGER NOT NULL);
```

For bucket `key` and row `i`, insert `mix64(seed + G*(key+1) + G*(i+1)) % 1000000`, with the same specified unsigned modulo-2^64 generator as other adapters. Each bucket has `max(light_rows, heavy_rows)` rows. The connection then becomes query-only. Each request executes:

```sql
SELECT COALESCE(SUM((seq+1)*value),0)
FROM items WHERE bucket=?1 AND seq<?2;
```

Parameters are the request's key and its light/heavy row count. A Python integer reduction independently calculates every expected response without SQLite or SQL. Maximum supported sums fit a signed 64-bit integer. Fixed values for seed 17 include key 1/eight rows = 13,580,604 and key 0/256 rows = 16,310,960,696.

## Alternatives and legality

`scan` has no lookup index. `indexed` executes `CREATE INDEX items_lookup ON items(bucket,seq)` before the arrival window. Both disable automatic indexes and execute the identical prepared aggregate query, binding parameters for every request. Neither caches results, bypasses SQL, changes rows, or drops work. The observable contract excludes schema introspection and physical row order. [SQLite's query-planner documentation](https://www.sqlite.org/queryplanner.html) explains the table-scan and indexed-search mechanisms; this is a hypothesis for measurement, not a guaranteed benefit.

One connection and prepared statement are protected by the traced application mutex. Worker execution is consequently serialized at the connection; this does not model a multi-connection database. The region covers parameter binding, SQL execution, integer result checking and statement reset. Lock ownership includes the region, matching the original coarse trace grammar.

Preparation includes table creation, insertion, index construction and worker startup. New engines record its wall time, CPU and peak RSS in `setup.csv`. It is outside steady-state request quantiles and inside total investigation time. Peak RSS includes prepared data and index memory. The index may improve query latency while increasing preparation cost and memory; the report retains those costs. No cold-start, write-throughput or disk-I/O win is implied.

```sh
python3 tools/jitter.py investigate examples/sqlite-investigation.json --output results/sqlite
python3 tools/jitter.py report results/sqlite
```
