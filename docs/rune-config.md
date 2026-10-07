# The `rune.json` project config

The RUNE server tools are **config-driven**: the server names no commands and
no vendors — every command it runs comes from a `rune.json` in the target
project (workspace root). The file is ordinary, version-controlled JSON, so it
is code-reviewed like any other change and executes nothing on load; each
entry is a shell string the server invokes explicitly, visibly in the call.

## Schema

| Key | Required | Meaning |
|---|---|---|
| `suite_cmd` | ✓ | The measurement suite. **Protocol:** exits 0 and prints on stdout a single JSON object `{"cases": [{"id": str, "checksum": str, "time_s": float, "spills": int}, ...]}`. `checksum` is opaque (typically a digest of the complete output state); `time_s` is the serialized wall-clock for that case; `spills` is optional (register-spill bytes or the platform's equivalent). Text before the JSON object is tolerated (build noise), so the suite can reuse an existing runner that prints logs first. |
| `state_dir` | ✓ | Directory for the RUNE state: `goldens.json`, `ledger.json`, the serialization lock, and re-baseline archives. Inside the project. |
| `build_cmd` | — | Build run before each seal/gate. Absent = the caller guarantees the tree is built. |
| `work_dir` | — | `cwd` for every configured command. Defaults to the workspace root. |
| `dump_cmd` | — | Disassembler/dumper for `rune_dump`; `{input}` and `{output}` are replaced with the tool's `target`/`out_path` arguments. |
| `assemble_cmd` | — | Assembler for `rune_assemble`; same `{input}`/`{output}` substitution. |

## The state directory

- `goldens.json` — `{"cases": {"<id>": "<checksum>"}}`, sealed by `rune_seal`.
  The immutable reference; re-sealing is an explicit re-baseline (the old
  goldens and ledger move to `archive/` with a timestamp).
- `ledger.json` — `{"runs": [...]}`, append-only. Each gate entry carries:
  candidate label, tier, per-case medians, bit-exact pass/fail, spill totals,
  the median-total delta vs the incumbent, and the decision
  (`pending` → `accepted`/`rejected` via `rune_mark`, with the backport
  outcome `none`/`attempted`/`adopted`/`lossy` for T1/T2 entries).
- `.rune_lock` — the serialization gate: `rune_seal` and `rune_gate` refuse to
  run while another measurement run is live, because concurrent timing runs
  cross-contaminate. Stale locks (dead pid) are reclaimed automatically.

## Example (CUDA / V100, anonymized)

```json
{
  "build_cmd": "make -f Makefile.cmake -j8 install",
  "suite_cmd": "python3 tools/perf/rune_suite.py --configs 444,666,888",
  "state_dir": ".rune-state",
  "work_dir": ".",
  "dump_cmd": "/usr/local/cuda/bin/cuobjdump -sass {input} > {output}",
  "assemble_cmd": "python3 tools/perf/rune_assemble.py --sass {input} --object {output}"
}
```

The suite runner (any language) must produce the protocol JSON. The minimal
core of one is:

```python
import json, subprocess, hashlib, time
cases = []
for cfg_id in ("444", "666", "888"):
    t0 = time.monotonic()
    out = subprocess.run(["build/app_harness", cfg_id], check=True, capture_output=True)
    dt = time.monotonic() - t0
    md5 = hashlib.md5(out.stdout).hexdigest()
    cases.append({"id": cfg_id, "checksum": md5, "time_s": round(dt, 6), "spills": 0})
print(json.dumps({"cases": cases}))
```

## Design rules for a config

1. **The suite replays the real entry points** — never a simplified copy of
   the code under test; a stub that reimplements what the project imports
   proves nothing about the project.
2. **One measurement at a time.** The runner must not parallelize the
   configurations; medians come from `repeats` sequential passes.
3. **Checksums cover the complete output state**, not a sample — a single-bit
   divergence must fail the gate.
4. **The spill source** (verbose-compiler log, binary size, cache footprint)
   is whatever the platform offers; the gate reports the delta vs the
   incumbent, and the zero-new-resources rule is applied by the reviewer.
