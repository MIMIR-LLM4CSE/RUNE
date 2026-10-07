# RUNE — Rapid Unmediated Native Executables

A [MIMIR](https://github.com/MIMIR-LLM4CSE/MIMIR) skill that specializes the
agent into a **machine-code-level auto-tuner**: it optimizes codes by
directly editing the compiled artifact — SASS, assembly, object code — and
transforms natural-language performance goals into native executables,
bypassing the compiler where the compiler leaves performance on the table.

Wherever an optimization can be expressed back in the source language
(CUDA, C, Fortran, …) **without losing the measured gain**, RUNE backports
it: the shipped artifact is then an ordinary, reviewable source change. The
disassembly stays behind as evidence. Only when no language spelling
reproduces the gain does the machine-code patch itself ship.

> From math, to HPC — and one step further: from language, to machine code,
> and back.

## The tier model

| Tier | Surface | Numerics | Ships as |
|---|---|---|---|
| **T0** | Language-code knobs (occupancy, block shape, flags) | bit-exact | source change |
| **T1** | Machine code, edited and reassembled | bit-exact | **backport** to source if the gain survives (within noise); else the patch |
| **T2** | Machine code | bit-exact | reproducible binary-patch pipeline (backport proven lossy) |
| **T3** | Any | **changes results** | whatever the tier-0..2 surface is; gated by an application-owned error budget |

Two invariants cut across all tiers:

- **Bit-exact by default.** Without an explicit, user-provided error budget
  (T3 only), an accepted optimization reproduces the baseline output
  byte-for-byte — verified against sealed goldens, not spot checks.
- **Measurement only through the sanctioned path.** A proxy/evaluation
  suite (sealed references + serialized, one-at-a-time timing + per-config
  pass/fail) is the only source of truth. Numbers taken outside it do not
  count.

## How it works

1. **Seal** a baseline: build with the project's own scripts, record
   goldens and serialized timings on the target hardware.
2. **Profile** with hardware counters; rank candidate transformation
   classes by measured evidence (latency- vs throughput-bound, coalescing,
   bank conflicts, occupancy, spills), not intuition.
3. **Ratchet**: for each candidate in the top class — edit, gate
   (bit-exact + spills + timing beyond replicate spread vs incumbent),
   record, accept iff all gates pass. Monotone: never regress.
4. **Backport** accepted machine-code edits to the source language;
   re-measure; keep the source spelling when the gain survives.
5. **Prove gate-off identity**: platform-gated tunings leave gate-off
   builds byte-identical at the machine-code level.
6. **Document** the measurement (numbers, replicates, spread, hardware,
   toolchain) in the shipped artifact.

The full methodology is the skill body: [`.mimir/skills/rune/SKILL.md`](.mimir/skills/rune/SKILL.md).
The tier decision tree and gate definitions: [`docs/architecture.md`](docs/architecture.md).
A complete worked engagement (production seismic wave-propagation kernels
on V100, from occupancy tuning to a SASS-guided sweep to a CI-integration
loop): [`docs/example-v100-case.md`](docs/example-v100-case.md).

## Installing

RUNE is a MIMIR extension pack (see MIMIR's
[plugin guide](mimir/PLUGINS_DETAILED.md)). The repo tree is already the
MIMIR drop-in layout — install the components you want by copying or
symlinking them into your workspace:

```sh
git clone --recurse-submodules https://github.com/MIMIR-LLM4CSE/RUNE.git
ln -s "$(pwd)/RUNE/.mimir/skills/rune"   <your-workspace>/.mimir/skills/rune
ln -s "$(pwd)/RUNE/.mimir/servers/server_rune.py"  <your-workspace>/.mimir/servers/
ln -s "$(pwd)/RUNE/.mimir/plugins/rune_nudges.py"  <your-workspace>/.mimir/plugins/
```

Then invoke the skill explicitly (`/rune <goal>`) or let MIMIR's classifier
detect an auto-tuning task. The server's tools (`rune_seal`, `rune_gate`,
`rune_mark`, `rune_dump`, `rune_assemble`, `rune_ledger`) become available as
the `rune` namespace, and the nudges fire by themselves. The tools are
**config-driven and architecture-neutral**: they run whatever the target
project's `rune.json` names (build command, suite runner, dump/assemble
pair) — schema and an example in [`docs/rune-config.md`](docs/rune-config.md).
If the project has no suite yet, build one first (or ask RUNE to); the skill's
step 1 exists for that case.

## Repository layout

```
.mimir/skills/rune/SKILL.md   # the MIMIR skill: methodology, tiers, workflow
.mimir/servers/server_rune.py # MCP server: the sanctioned measurement path as tools
.mimir/plugins/rune_nudges.py # nudges: backport-owed and rejection-record reminders
docs/architecture.md          # tier decision tree, gates, backport acceptance rule
docs/rune-config.md           # the rune.json schema + example
docs/example-v100-case.md     # worked example: CUDA/V100, bit-exact, CI-integrated
mimir/                        # MIMIR, pinned as a git submodule (see .gitmodules)
```

## Status and provenance

The methodology was developed and validated end-to-end on a production
seismic-imaging code (wave-propagation kernels, NVIDIA V100, CUDA 11.0):
occupancy and block-shape tuning shipped bit-exact through PR review and
CI; SASS-guided candidate ranking; a proxy-ratchet measurement suite; a
Debug/Release toolchain regression diagnosed from CI logs and fixed. That
engagement is documented in [`docs/example-v100-case.md`](docs/example-v100-case.md)
and serves as the template case study.

License: LGPL-2.1 (matching the MIMIR submodule). Contributions welcome —
especially worked case studies on other architectures.
