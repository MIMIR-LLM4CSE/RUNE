---
name: rune
description: Make existing code run faster on target hardware (GPU/CPU) while keeping results bit-identical — performance-tune kernels, dump and edit disassembly/assembly, backport machine-level wins to source, or sweep code-generation knobs; measured through a benchmark/proxy suite.
---

Objective: auto-tune the target code by treating **machine code as the primary
optimization surface**. RUNE replaces the compiler's role where the compiler
leaves performance on the table: it disassembles the production artifact,
edits the machine code (or the language code that reproduces it), and proves
each step correct. Natural language in, native executables out — the
compiler becomes a starting point, not an oracle.

## Doctrine

1. **Bit-exactness is the default contract.** Unless the user explicitly
   grants an error budget, an optimization is valid only if the artifact's
   output is byte-for-byte identical to the baseline. A faster wrong answer
   is a regression, not a result.
2. **The compiler is a baseline, not an oracle.** Its output is evidence of
   what the hardware accepts, not a statement of what is optimal. Dump it,
   measure it, and treat its register allocation and scheduling as
   hypotheses.
3. **Only sanctioned measurements count.** Every candidate is judged by the
   project's proxy/evaluation path — sealed goldens, serialized one-run-at-a-
   time timing, replicate spreads. Timing taken outside that path is not a
   result; never optimize against it.
4. **The ratchet is monotone.** A candidate is accepted only if it passes
   the correctness gate for its tier AND improves the primary metric beyond
   replicate spread. Rejected candidates are recorded with their numbers,
   not silently dropped.
5. **Backport when it is free.** Language code is the reviewable,
   maintainable artifact. Whenever a machine-code edit can be expressed in
   the source language without losing the measured gain (within noise),
   ship the language change instead. The disassembly remains the evidence;
   the PR carries the fix.
6. **Evidence ranks candidates; intuition does not.** Profile with hardware
   counters before choosing a transformation class: latency- vs
   throughput-bound, transaction/coalescing efficiency, bank conflicts,
   occupancy, spill counts. Rank candidate classes by what the counters
   show, then sweep the highest-ranked class.
7. **Gated changes must be inert when off.** Any platform-specific tuning
   lives behind a gate (arch/CC/feature). With the gate undefined, the build
   must be byte-identical to the pre-change build — verified at the machine-
   code level, not assumed.
8. **The measurement is part of the artifact.** Numbers, units, replicate
   counts, spread, hardware, toolchain version — recorded in the artifact's
   own docs, so the next maintainer can reproduce the decision.

## Tiers

Work the cheapest tier that the evidence supports; escalate only on
measured evidence.

**T0 — Language-code tuning, bit-exact.** Knobs inside the compiler
contract: launch bounds and occupancy, block/grid shape, scheduling
pragmas, compiler flags. Gates: bit-exact output vs sealed goldens, no new
spills (or a spill budget the user set), timing beyond replicate spread.
Always backportable by construction — this is where most cheap wins live.

**T1 — Machine-code edit with backport.** Dump the production machine code
with the project's own toolchain (disassembler; e.g. SASS dumps, objdump).
Identify instruction-level waste: poor scheduling, register spills,
redundant memory traffic, missed vector width. Edit the disassembly,
reassemble, link, then: (a) verify bit-exact vs goldens, (b) measure through
the proxy path, (c) **attempt the backport** — express the same
transformation in the source language (restructuring, intrinsics, bounds,
padding) and re-measure. Ship the backport if it reproduces the gain within
noise; ship the machine-code patch only if it measurably does not.

**T2 — Machine-code edit, no backport.** For gains that depend on register
allocation or instruction scheduling no language spelling reaches: ship
the assembled object as the artifact. Requirements: bit-exact vs goldens, a
reproducible patch pipeline (baseline dump → edit → assemble → object
replacement in the build), and a written statement of why backport failed,
with the failed attempt's numbers. The maintenance burden of a binary patch
is a cost the gain must clear.

**T3 — Non-bit-exact.** Numerics-changing optimizations (fusion,
reassociation, precision changes, temporal blocking that alters accumulation
order). The error budget belongs to the application owner, never to the
agent: require an explicit user-provided bound (e.g. l2_rel vs a reference
on production-representative inputs) before starting, and gate on it. Never
mix a T3 change silently into a T0–T2 claim.

## Workflow

1. **Seal the baseline.** Build with the project's own scripts. Record
   reference outputs (goldens, checksums) from the unmodified artifact and
   baseline timings through the proxy path. Serialize: one measurement run
   at a time on the target hardware. When the project has a `rune.json`
   (schema: `docs/rune-config.md` in the RUNE repo), `rune_seal` does this;
   re-sealing is an explicit re-baseline (`rebaseline=true`), never a
   silent overwrite.
2. **Profile for evidence.** Collect hardware-counter profiles of the
   dominant kernels/critical sections. Write down, per candidate class, what
   the counters say for and against it.
3. **Rank and sweep.** Take the top-ranked class; enumerate candidates;
   for each: edit (language or machine code), rebuild, gate (bit-exact +
   spill + timing vs incumbent), record. Accept iff all gates pass. With a
   `rune.json`, `rune_gate` runs the serialized pass and returns the gates'
   measurements; `rune_mark` records the decision (and the backport
   outcome); `rune_ledger` reads the incumbent and history. The tools
   measure — the acceptance judgment is yours, recorded through the normal
   verdict path.
4. **Attempt the backport** for every accepted machine-code edit (T1 rule).
   Re-measure the backport against the machine-code candidate; keep the
   language spelling if within noise. With the tools: gate the backport and
   mark the outcome `adopted` or `lossy` — the failed attempt's numbers are
   what justify a T2 artifact.
5. **Verify gate-off identity** for any gated change: rebuild with the gate
   undefined and diff the machine code against the pre-change artifact
   (`rune_dump` gives both sides).
6. **Ship and document.** The PR carries the language change (or the patch
   pipeline for T2), the updated gate definitions, and the measured table:
   candidate → gates → timings → decision.

## Notes

- All tool names (dump/disassemble/assemble/profile) come from the
  project's config; this skill names no vendor tools. Examples for one
  platform live in `docs/example-v100-case.md`.
- Tier boundaries are empirical: a candidate starts at T1 and becomes T2
  only when the backport attempt measurably fails — record the attempt.
- If no proxy/evaluation path exists, build one before optimizing: goldens
  + serialized timings + per-config pass/fail, then a `rune.json` naming it
  (schema: `docs/rune-config.md`) so the RUNE tools and nudges can drive
  it. Measuring without a sanctioned path is guesswork with extra steps.
