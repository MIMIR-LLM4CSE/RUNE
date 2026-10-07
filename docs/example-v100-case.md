# Example engagement: V100 wave-propagation kernels — the case RUNE was distilled from

The RUNE methodology was not designed top-down; it was crystallized from a
complete production engagement: bit-exact performance optimization of the
CUDA wave-propagation kernels of a production seismic-imaging code
(acoustic, visco-acoustic, elastic; forward and adjoint propagators) on an
HPC cluster with NVIDIA V100 GPUs (CUDA 11.0.3), shipped through
pull-request review and CI on the project's clusters. Names and identifiers
are genericized; the numbers and the decisions are real. This document
records what was done, at which tier, with the measured outcomes — as a
template for writing case studies on other architectures.

Platform facts that shaped everything: V100 has 65536 registers/SM, so
co-residency jumps at fixed register cliffs; the vendor's register allocator
leaves many kernels one cliff below what the register file allows. The CUDA
toolchain version was pinned by the production environment, so "upgrade the
compiler" was not an option — the exact situation RUNE exists for.

## T0: occupancy tuning, shipped as source

**Evidence.** A full `-Xptxas -v` census of every kernel: register counts,
spill bytes, and the `__launch_bounds__` the build carried. Cliff analysis:
how many blocks/SM fit at each register count vs what the build requested.

**Method.** For each kernel near a cliff: raise `min_blocks` (via
`__launch_bounds__`), rebuild, gate on (a) bit-exact MD5s of the complete
wavefield state vs goldens sealed from the unmodified build, over all
stencil configurations; (b) zero new spill bytes; (c) serialized timing,
3 replicates, one run at a time.

**Outcomes (family-level wall clock, medians of 3).**

| family | change | effect |
|---|---|---|
| acoustic (isotropic) direct | 11 of 12 kernels, +1..+2 blocks | −4.8% |
| acoustic (transverse-isotropic) direct / adjoint | cliff-adjacent +1 block pairs | −1.75% / −1.34% |
| visco-acoustic (tilted) direct / adjoint | stress/velocity kernel pairs | −2.74% / −2.27% |

**Equally important — the rejections, recorded:** the anisotropic-acoustic
direct push (+5.7% family, 296 spill bytes); the adjoint family's
full-pair push (forced register cuts on 117–128-register kernels lost
5–40× what the extra block bought); elastic curvilinear (sub-noise); the
gradient kernel (88 new spill bytes — rejected by the resource gate alone).
The rejection record is what turned the sweep into an operating rule: gains
live at 2–6 blocks/SM with a register gap ≤ 21 to the next cliff;
near-128-register kernels never pay; non-binding bounds must be left alone
(raising them changed nothing in the allocator's register counts yet
regressed scheduling).

**Shipped as pure source** — the tuned values behind a build gate keyed on
the compute capability, verified byte-identical at the SASS level
(sm_70 and sm_80) when the gate is off.

## T0→T1: profiler-ranked block-shape sweep (round 2)

**Evidence first.** Nsight Compute over every family: SM throughput 8–43%
(latency-bound, not transaction-bound), global sectors/request 2.5–4.2
against an ideal 4.0 (coalescing already near-perfect), shared bank
conflicts 0–8% of wavefronts. That evidence *demoted* two candidate classes
the literature would have ranked first — vectorization (instruction
economy only, no fewer transactions) and shared-memory padding (no kernel
flagged above noise) — and promoted launch geometry.

**Method.** Swept `(threads, threads_x)` shapes of the four 256-thread
stress kernels through the same three gates. One accepted: the
visco-acoustic direct stress kernel, (256,32)→(128,32) — −3.1% on the
critical configuration (spread 0.3%), −0.9% family, bit-exact, spills
unchanged. Siblings sub-noise at family level; all recorded. Shipped as
source, gate-off SASS identity re-verified.

## T1/T2: SASS surgery, assessed and correctly deferred

The engagement included the full T1 apparatus: `cuobjdump -sass` dumps of
the production kernels as the baseline to imitate, disassembly editing
with reassembly, bit-exact verification against the CUDA baseline. The
honest outcome: after T0 captured the cliff/geometry gains, the remaining
SASS candidates were spill-blocked kernels where register surgery could not
be backported (T2-only, with the maintenance burden of binary patches) —
and the measured evidence (latency-bound at 8–43% SM throughput) did not
show occupancy binding hard enough to justify that burden. **Deferral with
evidence is a RUNE decision**: the T2 pipeline stays documented, the
trigger for revisiting it (profiler showing occupancy binding) is written
down.

## The proxy suite (the measurement path)

Built before optimizing: per-family harnesses replaying deterministic
timesteps through the *real* wrappers at all stencil configurations;
goldens sealed from the reference kernels (full-state MD5s); suite runners
emitting pass/fail + median timing per config; baseline-times files as the
ratchet incumbent. All timing serialized (one suite at a time — concurrent
GPU sweeps cross-contaminate through the device picker). The ratchet
consumed only proxy-path results.

## Toolchain regression: the CI loop as a backport-safety story

The PR briefly bumped a build-system submodule to obtain a new variable;
that revision dropped the OpenACC/OpenMP-offload defines from Debug
builds, and a CUDA-Fortran kernel target stopped compiling
(`NVFORTRAN-S-0528`: host arrays reaching a device interface because a
header macro expanded to nothing). Diagnosed from CI job logs, reproduced
locally in a worktree build, fixed by reverting to the revision the base
branch pins — and the whole episode validated the doctrine twice over:
gate-off byte-identity is what protects other platforms, and **the fix that
ships must be the one that survives review** — here, dropping the submodule
bump entirely rather than carrying a private toolchain patch.

## Case-study lessons (the transferable part)

1. The census-and-cliff analysis came before any sweep; the first accepted
   candidate was found by arithmetic on `-Xptxas -v` output, not by trial.
2. The rejection record paid for itself: it became the operating rule that
   cut the sweep space for every later family.
3. Profiler evidence overruled the two "obvious" candidate classes; the
   accepted class was the third one.
4. Every shipped change was bit-exact by construction (gates) and
   byte-identical when gated off — so the PR review argued about numbers,
   never about correctness.
5. T2 was declined with evidence, not ignored — the difference between a
   methodology and a habit.
