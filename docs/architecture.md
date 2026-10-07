# RUNE architecture: tiers, gates, and the backport rule

This document defines the decision structure behind
[`skills/rune/SKILL.md`](../skills/rune/SKILL.md). It is deliberately
architecture-neutral: every concrete tool name lives in the project's own
config or in a case study.

## The optimization surface

RUNE treats the compiled artifact as editable source:

```
source language  --(project toolchain)-->  machine code
       ^                                      |
       |                                      v
   backport (T1)                     dump / disassemble
       |                                      |
       v                                      v
   shipped change  <---(gates)----  edited machine code
```

The compiler produces the baseline. RUNE reads the baseline as a hypothesis
about register allocation, scheduling, and memory traffic — then tests
better ones, either in language code (T0), by editing the disassembly and
reassembling (T1/T2), or by changing the computation itself under an
explicit error budget (T3).

## Tier decision tree

```
                 ┌─ can it be expressed as a language-level
                 │  knob the compiler contract already allows?
                 │  (bounds, shape, flags, pragmas)
        yes ─────┤
                 v
                T0   cheapest; always ships as source.
                 │
                 │ does the evidence show instruction-level waste the
                 │ compiler will not fix from any knob? (spills, poor
                 │ scheduling, redundant traffic)
        no ──────┤
                 v
                T1   edit the disassembly, reassemble, gate bit-exact.
                 │    then REQUIRE a backport attempt:
                 │      language spelling reproduces the gain within noise?
                 │        yes → ship the source change (tier satisfied at T0/T1 cost)
                 │        no  → record the failure; the candidate is T2
                 v
                T2   ship the machine-code patch as the artifact:
                     reproducible pipeline (dump → edit → assemble →
                     object replacement) + documented why-backport-fails.
                 │
                 │ is the gain only available by changing the numerics?
        no ───────┤
                 v
                T3   application-owned error budget required BEFORE start;
                     gate on the bound; never mix with a bit-exact claim.
```

Escalation is empirical: a candidate enters at the lowest plausible tier and
is demoted only by a measured failure, which is recorded.

## Gates

Every candidate, at every tier, passes through the same three gates:

1. **Correctness gate (tier-dependent).**
   - T0–T2: bit-exact — full-state comparison against goldens sealed from
     the unmodified baseline build (checksums or byte compares of the
     output state, over all production configurations). A single-bit
     divergence rejects the candidate.
   - T3: the user-provided error bound vs a reference, measured on
     production-representative inputs, plus any invariants the application
     names (conservation, monotonicity, finiteness).
2. **Resource gate.** No new register spills beyond the baseline (or an
   explicit budget). Machine-code-level metrics (code size, cache footprint)
   within the project's budget if it declares one.
3. **Timing gate.** Median of ≥3 serialized replicates on the target
   hardware, compared against the incumbent; accept only beyond the
   replicate spread (or the suite's configured minimum improvement). One
   measurement run at a time; concurrent timing runs cross-contaminate.

A candidate failing any gate is rejected with its numbers recorded. The
ratchet never trades a gate for speed.

## The backport acceptance rule

An accepted T1 edit triggers a mandatory backport attempt:

```
gain(machine-code patch) = G_mc
gain(language spelling)  = G_lang
accept the language spelling iff  G_lang >= G_mc - noise
```

where `noise` is the replicate spread measured on the incumbent. Rationale:
the language change survives toolchain upgrades, code review, and refactors;
the binary patch does not. The backport attempt is part of the record even
when it fails — that record is what justifies the T2 artifact.

## Gate-off identity

Platform-specific tuning (any tier) is wired behind a build gate
(architecture / compute capability / feature flag). The non-negotiable
invariant:

> With the gate undefined, the produced machine code is byte-identical to
> the pre-change build.

Verified by dumping both objects and comparing (normalizing addresses), not
by trusting the build log. This is what lets a tuning ship for one platform
without perturbing every other platform and future toolchain.

## The proxy suite

If the project lacks a sanctioned measurement path, RUNE builds one first:

- **Sealed goldens**: reference outputs recorded from the unmodified build,
  per configuration; immutable once sealed. Re-sealing requires the
  incumbent's agreement (a re-baseline, never a silent overwrite).
- **Suite runner**: replays each configuration through the real entry
  points, compares against the goldens, and times — serialized.
- **Ratchet ledger**: every run's candidate, gates, and medians; accepted
  runs become the incumbent; rejected runs stay as evidence.

Optimization loops consume only proxy-path results. A hand-timed number is
an anecdote, not a measurement.

## Maintenance

Tuned values are facts about a pinned toolchain, not eternal truths. When
the toolchain or the hardware changes: re-census (spills, resources),
re-sweep the top candidates, keep only what still passes, and empty gated
sections the new compiler reaches on its own. The measurement record left
in the artifact (README pattern: one table per candidate class) exists so
this re-validation is a repetition, not an investigation.
