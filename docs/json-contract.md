# JSON projection integration

`json-projection-v1` processes JSON documents with the independently maintained
[nlohmann/json library](https://github.com/nlohmann/json). Jitter owns the worker
pool, generated request workload, document generator, and projection contract.
This is evidence from an upstream C++ library in a controlled application harness.
It does not establish performance in an independently maintained production
service or arbitrary JSON application.

## Pinned upstream source

Version 3.12.0, commit `55f93686c01528224f448c19128836e7df245f72`, is vendored
without changes under `third_party/nlohmann/`. The single header has SHA256
`aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63`, matching
the [official release](https://github.com/nlohmann/json/releases/tag/v3.12.0).
The MIT license and machine-readable file provenance accompany it. Builds verify
the header hash and report the version and hash in `--build-info`; builds and
replays do not require downloading this dependency.

## Request behavior

An application specifies `adapter: "json-projection-v1"`, implementation `map` or
`ordered`, `key_count`, `dataset_seed`, `light_fields`, and `heavy_fields`, plus
the common worker and queue settings. Field counts range from 1 through 4096.
Digest-round settings belong to the separate shared-index contract.

Before worker execution and the timed phases, prepare one light and one heavy
document for each dataset key. A document with `N` fields contains exactly the
unique names `f0` through `f(N-1)`, in that order, and unsigned integer values:

```
G = 0x9e3779b97f4a7c15
v(seed, key, i) = mix64(seed + G*(key+1) + G*(i+1)) % 1000000007
```

All arithmetic before the remainder is unsigned modulo 2^64. `mix64` is the
specified SplitMix64 finalizer already used by `shared-index-v1`. Names are ASCII;
the generated documents contain no duplicates, nesting, floating-point numbers,
comments, escape sequences, or caller-supplied bytes.

Each measured request briefly locks the immutable document index, obtains a
reference, and releases the lock. It then parses the entire selected document,
looks up every field by its explicit name, and returns:

```
sum((i+1) * parsed["f" + decimal(i)] for i in range(N)) modulo 2^64
```

Parsing and every field lookup occur inside the measured execution region.
Document preparation happens before the arrival window; process peak RSS still
includes its memory. Parsed documents are private to one request. Shared
documents and field-name strings remain immutable for the application lifetime.

## Alternatives and legality

`map` invokes `nlohmann::json::parse`; `ordered` invokes
`nlohmann::ordered_json::parse`. The library documents their different
[object storage and ordering](https://json.nlohmann.me/features/object_order/).
The projection observes integer values through named lookup and excludes object
iteration or serialization order, so both preserve this restricted contract.
This equivalence does not authorize replacing arbitrary user JSON types.

Both implementations parse every document on every request, perform the same
number of lookups, and return the same projection. Neither bypasses parsing or
caches responses. The vector-backed ordered representation can fare poorly on
large objects; it is a legitimate negative case rather than a promised
optimization.

The conservative preparation bound is
`key_count * (32*(light_fields + heavy_fields) + 128) <= 268435456` bytes.
The C++ constructor enforces it before allocating documents. It bounds reserved
document character storage and document string bookkeeping; parsed DOMs, worker
stacks, traces, allocator overhead, and the small shared name table additionally
consume memory and remain subject to measured resource constraints. This is not
a whole-process memory guarantee.

## Validation and traces

The independent oracle can generate field values directly and sum the projection
without invoking either C++ parser. Fixed values for seed 17 are:

| Key | Fields | Projection |
| --- | --- | --- |
| 0 | 1 | 444051756 |
| 0 | 3 | 812717509 |
| 1 | 8 | 17208641377 |
| 0 | 256 | 16464020396125 |
| 7 | 4096 | 4194286716285739 |

The lookup trace records `lock_wait`, `lock_acquire`, `lock_release`,
`region_begin`, and `region_end`. Its region uses the existing resource ID 3;
for this adapter that region means JSON parsing and projection. It does not
overlap the observed index-lock ownership interval. Event counts and raw request
accounting are unchanged.

The shared-index `narrow` variant uses the same release-before-region trace
shape. Its legality condition is that the dataset remains immutable and the
digest depends only on the copied value, key, and round count. The original
`coarse` variant preserves the earlier region-inside-lock event sequence.

## Isolated engines

`-DJITTER_VARIANT=coarse|narrow|map|ordered` produces an engine that rejects any
other requested implementation. `--build-info` records `compiled_variant`.
The default `any` engine supports every registered alternative and preserves
legacy commands; the full core test suite uses this default. Isolated candidate
builds can set `BUILD_TESTING=OFF` and are validated through their request oracle.
The alternatives remain explicit reviewed implementations; no arbitrary C++
source rewrite is performed.
