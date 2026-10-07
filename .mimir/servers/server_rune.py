"""RUNE MCP server — the sanctioned measurement path as config-driven tools.

Copy this into your servers directory to activate it — `$MIMIR_SERVERS_DIR` or
`<workspace>/.mimir/servers/`. The tool namespace is the filename stem with any
`server_` prefix stripped (`server_rune.py` -> `rune`).

RUNE methodology: bit-exact optimization by editing machine code directly,
backporting to source language whenever the gain survives; every candidate is
judged only by measurements taken through this path. The server itself is
architecture-neutral: every command it runs comes from the project's
`rune.json` (schema: docs/rune-config.md in the RUNE repo). The server names
no vendor tools and invents no commands.

Protocol contract with the project's suite runner (defined by RUNE, documented
in docs/rune-config.md): `suite_cmd` exits 0 and prints on stdout a single
JSON object {"cases": [{"id": str, "checksum": str, "time_s": float,
"spills": int}, ...]}. checksums are opaque strings; goldens store them,
gates compare them.

This server measures; it never judges. Verdicts on what a run showed are
recorded through the client's normal verdict path — no tool here declares
JUDGE.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mimir.servers._shared.capabilities import tool_caps, READ, CACHEABLE, CODE_EXEC, EDIT
from mimir.servers._shared.responses import ok, err
from mimir.servers._shared.root_paths import require_absolute

try:  # canonical root, same import the client uses
    from mimir.config.constants import WORKSPACE_ROOT
except Exception:  # pragma: no cover - fallback for isolated imports
    WORKSPACE_ROOT = os.path.abspath(os.getcwd())

mcp = FastMCP("rune")

_REQUIRED_KEYS = ("suite_cmd", "state_dir")
_OUT_TAIL = 2000


def _abs(config: str) -> "dict | None":
    return require_absolute(config, WORKSPACE_ROOT, arg="config")


def _load_config(config: str) -> "tuple[dict, dict] | tuple[None, dict]":
    """Return (cfg, None) or (None, err-payload)."""
    bad = _abs(config)
    if bad:
        return None, bad
    path = Path(config).expanduser()
    if not path.is_file():
        return None, err(
            f"RUNE config not found at {config}.",
            hint="Write a rune.json at the workspace root (schema: docs/rune-config.md "
                 "in the RUNE repo) naming the build command, suite runner, and "
                 "dump/assemble pair for this platform.",
        )
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, err(f"rune.json is not valid JSON: {exc}", hint="Fix the syntax and retry.")
    missing = [k for k in _REQUIRED_KEYS if not str(cfg.get(k) or "").strip()]
    if missing:
        return None, err(
            f"rune.json is missing required key(s): {', '.join(missing)}.",
            hint="See docs/rune-config.md in the RUNE repo for the schema.",
        )
    return cfg, None


def _state(cfg: dict) -> Path:
    return Path(str(cfg["state_dir"])).expanduser().resolve()


def _lock_path(cfg: dict) -> Path:
    return _state(cfg) / ".rune_lock"


def _acquire_lock(cfg: dict) -> "dict | None":
    """One measurement run at a time — concurrent timing runs cross-contaminate."""
    lock = _lock_path(cfg)
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or "0")
            os.kill(pid, 0)  # raises if dead
        except (ProcessLookupError, ValueError, PermissionError, OSError):
            pass  # stale lock from a dead run: overwrite
        else:
            holder = "another RUNE measurement run"
            try:
                out = subprocess.run(["ps", "-o", "args=", "-p", str(pid)],
                                     capture_output=True, text=True, timeout=10)
                if out.stdout.strip():
                    holder = out.stdout.strip()[:200]
            except Exception:
                pass
            return err(
                "A RUNE measurement run is already in progress (serialization gate).",
                hint=f"Lock held by pid {pid} ({holder}). Concurrent timing runs "
                     "cross-contaminate; wait for it to finish before gating another "
                     "candidate.",
            )
    lock.write_text(str(os.getpid()))
    return None


def _release_lock(cfg: dict) -> None:
    try:
        _lock_path(cfg).unlink()
    except OSError:
        pass


def _run_cmd(cmd: str, cfg: dict, timeout_s: int) -> "tuple[dict | None, dict | None, str]":
    """Run a configured command; return (stdout, None, tail) or (None, err, tail)."""
    cwd = str(cfg.get("work_dir") or WORKSPACE_ROOT)
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                               text=True, timeout=timeout_s or 1800)
    except subprocess.TimeoutExpired:
        return None, err(f"Command timed out after {timeout_s}s: {cmd[:120]}",
                          hint="Raise timeout_s or narrow what the command runs."), ""
    tail = (proc.stdout + "\n" + proc.stderr)[-_OUT_TAIL:].strip()
    if proc.returncode != 0:
        return None, err(
            f"Command failed (exit {proc.returncode}): {cmd[:120]}",
            output_tail=tail[-800:],
        ), tail
    return proc.stdout, None, tail


def _parse_suite(stdout: str) -> "tuple[dict | None, dict | None]":
    """Parse the suite protocol JSON; tolerate leading noise before the JSON object."""
    text = stdout.strip()
    start = text.find("{")
    if start < 0:
        return None, err("Suite output contains no JSON object.",
                         hint="suite_cmd must print {\"cases\": [...]} on stdout.")
    try:
        data = json.loads(text[start:])
    except json.JSONDecodeError as exc:
        return None, err(f"Suite output is not valid JSON: {exc}")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return None, err("Suite JSON has no non-empty 'cases' array.")
    for c in cases:
        if not c.get("id") or not isinstance(c.get("checksum"), str):
            return None, err("Each suite case needs an 'id' and a string 'checksum'.")
    return data, None


def _median(xs: list) -> "float | None":
    xs = sorted(v for v in xs if isinstance(v, (int, float)))
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _read_ledger(cfg: dict) -> list:
    p = _state(cfg) / "ledger.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("runs", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _incumbent(cfg: dict) -> "dict | None":
    for run in reversed(_read_ledger(cfg)):
        if run.get("decision") == "accepted":
            return run
    return None


def _append_ledger(cfg: dict, entry: dict) -> None:
    st = _state(cfg)
    st.mkdir(parents=True, exist_ok=True)
    p = st / "ledger.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"runs": []}
    except Exception:
        data = {"runs": []}
    runs = data.get("runs", [])
    entry["id"] = f"run-{len(runs) + 1:04d}"
    entry["schema"] = 1
    runs.append(entry)
    p.write_text(json.dumps({"runs": runs}, indent=1), encoding="utf-8")


def _case_list(cases: list, times: "dict[str, list]") -> list:
    out = []
    for c in cases:
        cid = c["id"]
        out.append({
            "id": cid,
            "checksum": c["checksum"],
            "time_s": _median(times.get(cid, [])),
            "spills": c.get("spills"),
        })
    return out


@mcp.tool(**tool_caps(caps=[CODE_EXEC], reversibility="recoverable", path_args=["config"]))
def rune_seal(config: str, rebaseline: bool = False, timeout_s: int = 3600) -> dict:
    """Seal the RUNE baseline: build, record goldens and serialized baseline timings.

    First call only — the goldens become the immutable reference. Re-sealing
    (rebaseline=true) is an explicit re-baseline: it archives the old goldens
    and ledger, and must only be used when the reference itself changed (a
    sanctioned re-baseline, never a silent overwrite to make a candidate pass).

    Args:
        config: Absolute path to the project's rune.json.
        rebaseline: Pass true to re-baseline (archives the previous reference).
        timeout_s: Per-command wall-clock budget in seconds.
    """
    cfg, bad = _load_config(config)
    if bad:
        return bad
    st = _state(cfg)
    goldens_p = st / "goldens.json"
    if goldens_p.is_file() and not rebaseline:
        return err(
            "Goldens already sealed. A candidate must not move the reference.",
            hint="This is the ratchet: gate candidates with rune_gate. Pass "
                 "rebaseline=true only for a sanctioned re-baseline.",
        )
    lock = _acquire_lock(cfg)
    if lock:
        return lock
    try:
        if str(cfg.get("build_cmd") or "").strip():
            out, bad, tail = _run_cmd(str(cfg["build_cmd"]), cfg, timeout_s)
            if bad:
                return bad
        out, bad, tail = _run_cmd(str(cfg["suite_cmd"]), cfg, timeout_s)
        if bad:
            return bad
        data, bad = _parse_suite(out)
        if bad:
            return bad
        if rebaseline:
            _archive(cfg, st)
        st.mkdir(parents=True, exist_ok=True)
        cases = {c["id"]: c["checksum"] for c in data["cases"]}
        goldens_p.write_text(json.dumps({"cases": cases}, indent=1), encoding="utf-8")
        entry = {
            "kind": "seal", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "candidate": "baseline", "tier": "T0", "decision": "accepted",
            "backport": "none", "note": "sealed baseline" if not rebaseline else "re-baselined",
            "cases": _case_list(data["cases"], {c["id"]: [c.get("time_s")] for c in data["cases"]}),
        }
        entry["median_total_s"] = _median([e["time_s"] for e in entry["cases"]])
        _append_ledger(cfg, entry)
        return ok(action="seal", rebaseline=rebaseline,
                  goldens=str(goldens_p), cases=len(cases),
                  median_total_s=entry["median_total_s"],
                  run_id=entry["id"])
    finally:
        _release_lock(cfg)


def _archive(cfg: dict, st: Path) -> None:
    import shutil
    ts = time.strftime("%Y%m%dT%H%M%S")
    for name in ("goldens.json", "ledger.json"):
        p = st / name
        if p.is_file():
            arch = st / "archive"
            arch.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(arch / f"{name}.{ts}"))


@mcp.tool(**tool_caps(caps=[CODE_EXEC], reversibility="recoverable", path_args=["config"]))
def rune_gate(config: str, candidate: str, tier: str = "T0", repeats: int = 1,
              timeout_s: int = 3600, note: str = "") -> dict:
    """Run one serialized candidate pass: bit-exact gate + medians vs the incumbent.

    Builds (if build_cmd is set), runs the suite `repeats` times back-to-back
    (never concurrently — the lock enforces one measurement run at a time),
    compares every case checksum against the sealed goldens, and returns
    per-case pass/fail, median times, spill counts, and the median-total delta
    against the incumbent ledger entry. Records the run as decision "pending":
    accept or reject it with rune_mark. The server measures; you judge.

    Args:
        config: Absolute path to the project's rune.json.
        candidate: Short label for this candidate (goes into the ledger).
        tier: RUNE tier of this candidate — "T0", "T1", "T2" or "T3".
        repeats: Suite passes to run (>=1). Timings are medians across passes.
        timeout_s: Per-command wall-clock budget in seconds.
        note: Free-text context recorded with the entry (what was changed).
    """
    cfg, bad = _load_config(config)
    if bad:
        return bad
    if tier not in ("T0", "T1", "T2", "T3"):
        return err(f"Unknown tier '{tier}'.", hint='Use "T0", "T1", "T2" or "T3".')
    repeats = max(1, int(repeats or 1))
    st = _state(cfg)
    if not (st / "goldens.json").is_file():
        return err("No sealed goldens in this project.",
                   hint="Run rune_seal first — the ratchet needs a reference.")
    goldens = json.loads((st / "goldens.json").read_text(encoding="utf-8"))["cases"]
    lock = _acquire_lock(cfg)
    if lock:
        return lock
    try:
        if str(cfg.get("build_cmd") or "").strip():
            out, bad, tail = _run_cmd(str(cfg["build_cmd"]), cfg, timeout_s)
            if bad:
                return bad
        times: dict = {}
        last_cases, fails = None, []
        for _ in range(repeats):
            out, bad, tail = _run_cmd(str(cfg["suite_cmd"]), cfg, timeout_s)
            if bad:
                return bad
            data, bad = _parse_suite(out)
            if bad:
                return bad
            last_cases = data["cases"]
            for c in last_cases:
                times.setdefault(c["id"], []).append(c.get("time_s"))
                if goldens.get(c["id"]) != c["checksum"]:
                    fails.append(c["id"])
        cases = _case_list(last_cases, times)
        median_total = _median([c["time_s"] for c in cases])
        spills_total = sum((c.get("spills") or 0) for c in cases)
        inc = _incumbent(cfg)
        delta_pct = None
        if inc and isinstance(inc.get("median_total_s"), (int, float)) and median_total:
            delta_pct = round(100.0 * (median_total - inc["median_total_s"]) / inc["median_total_s"], 3)
        inc_spills = None
        if inc:
            inc_spills = sum((c.get("spills") or 0) for c in inc.get("cases", []))
        entry = {
            "kind": "gate", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "candidate": candidate, "tier": tier, "decision": "pending",
            "backport": "none", "note": note, "repeats": repeats,
            "bitexact_pass": not fails, "failed_cases": sorted(set(fails)),
            "cases": cases, "median_total_s": median_total,
            "spills_total": spills_total, "delta_vs_incumbent_pct": delta_pct,
        }
        _append_ledger(cfg, entry)
        return ok(action="gate", run_id=entry["id"], candidate=candidate, tier=tier,
                  bitexact_pass=entry["bitexact_pass"], failed_cases=entry["failed_cases"],
                  median_total_s=median_total, repeats=repeats,
                  spills_total=spills_total, incumbent_spills_total=inc_spills,
                  delta_vs_incumbent_pct=delta_pct,
                  verdict_hint=("all gates measurable — judge acceptance"
                                if entry["bitexact_pass"] else
                                "bit-exactness FAILED — reject this candidate"))
    finally:
        _release_lock(cfg)


@mcp.tool(**tool_caps(caps=[EDIT], path_args=["config"]))
def rune_mark(config: str, run_id: str, decision: str, backport: str = "none",
              note: str = "") -> dict:
    """Record the ratchet decision for a gate run in the ledger (bookkeeping, not a verdict).

    Accept only candidates that passed every gate AND improved beyond replicate
    spread; the ratchet is monotone. For an accepted T1/T2 machine-code edit,
    record the backport outcome: "adopted" (language spelling reproduces the
    gain within noise) or "lossy" (no spelling did — this is what justifies a
    T2 artifact).

    Args:
        config: Absolute path to the project's rune.json.
        run_id: Ledger run id from rune_gate (e.g. "run-0002").
        decision: "accepted" or "rejected".
        backport: "none" (T0 / not applicable), "attempted", "adopted" or "lossy".
        note: Context to record — for rejections, the measured numbers.
    """
    cfg, bad = _load_config(config)
    if bad:
        return bad
    if decision not in ("accepted", "rejected"):
        return err(f"decision must be 'accepted' or 'rejected', got '{decision}'.")
    if backport not in ("none", "attempted", "adopted", "lossy"):
        return err(f"backport must be none/attempted/adopted/lossy, got '{backport}'.")
    p = _state(cfg) / "ledger.json"
    if not p.is_file():
        return err("No ledger yet — nothing to mark.", hint="Run rune_gate first.")
    data = json.loads(p.read_text(encoding="utf-8"))
    for run in data.get("runs", []):
        if run.get("id") == run_id:
            if run.get("kind") != "gate":
                return err(f"{run_id} is not a gate run.")
            run["decision"] = decision
            run["backport"] = backport
            if note:
                run["note"] = (str(run.get("note") or "") + " | " + note).strip(" |")
            p.write_text(json.dumps(data, indent=1), encoding="utf-8")
            return ok(action="mark", run_id=run_id, decision=decision, backport=backport)
    return err(f"No ledger entry '{run_id}'.", hint="Call rune_ledger to list run ids.")


@mcp.tool(**tool_caps(caps=[CODE_EXEC], reversibility="recoverable", path_args=["config", "target", "out_path"]))
def rune_dump(config: str, target: str, out_path: str, timeout_s: int = 600) -> dict:
    """Dump machine code of a target through the project's configured disassembler.

    This is the T1/T2 baseline step: the production artifact's machine code is
    the evidence of what the compiler emitted. The command comes from rune.json
    ("dump_cmd") with {input} replaced by `target` and {output} by `out_path`.

    Args:
        config: Absolute path to the project's rune.json.
        target: Absolute path of the binary/object to dump.
        out_path: Absolute path of the disassembly file to write.
        timeout_s: Wall-clock budget in seconds.
    """
    cfg, bad = _load_config(config)
    if bad:
        return bad
    tmpl = str(cfg.get("dump_cmd") or "").strip()
    if not tmpl:
        return err("rune.json has no 'dump_cmd'.",
                   hint="Name this platform's disassembler/dumper in rune.json.")
    for arg, val in (("target", target), ("out_path", out_path)):
        chk = require_absolute(val, WORKSPACE_ROOT, arg=arg)
        if chk:
            return chk
    cmd = tmpl.format(input=target, output=out_path)
    out, bad, tail = _run_cmd(cmd, cfg, timeout_s)
    if bad:
        return bad
    return ok(action="dump", target=target, out_path=out_path, bytes=len(out or ""))


@mcp.tool(**tool_caps(caps=[CODE_EXEC], reversibility="recoverable", path_args=["config", "source", "out_path"]))
def rune_assemble(config: str, source: str, out_path: str, timeout_s: int = 600) -> dict:
    """Assemble an edited disassembly back into an object/binary via the configured assembler.

    The T1/T2 edit loop: rune_dump -> edit the disassembly -> rune_assemble ->
    rune_gate. The command comes from rune.json ("assemble_cmd") with {input}
    replaced by `source` and {output} by `out_path`.

    Args:
        config: Absolute path to the project's rune.json.
        source: Absolute path of the edited disassembly file.
        out_path: Absolute path of the object/binary to produce.
        timeout_s: Wall-clock budget in seconds.
    """
    cfg, bad = _load_config(config)
    if bad:
        return bad
    tmpl = str(cfg.get("assemble_cmd") or "").strip()
    if not tmpl:
        return err("rune.json has no 'assemble_cmd'.",
                   hint="Name this platform's assembler in rune.json.")
    for arg, val in (("source", source), ("out_path", out_path)):
        chk = require_absolute(val, WORKSPACE_ROOT, arg=arg)
        if chk:
            return chk
    cmd = tmpl.format(input=source, output=out_path)
    out, bad, tail = _run_cmd(cmd, cfg, timeout_s)
    if bad:
        return bad
    return ok(action="assemble", source=source, out_path=out_path, bytes=len(out or ""))


@mcp.tool(**tool_caps(caps=[READ, CACHEABLE], path_args=["config"]))
def rune_ledger(config: str, last: int = 10) -> dict:
    """Read the RUNE ratchet ledger: incumbent, goldens path, and recent runs.

    Use it before proposing the next candidate (what is the incumbent to beat,
    which candidates were already tried and rejected) and when writing the
    measurement record into the shipped artifact.

    Args:
        config: Absolute path to the project's rune.json.
        last: How many recent ledger entries to return.
    """
    cfg, bad = _load_config(config)
    if bad:
        return bad
    runs = _read_ledger(cfg)
    inc = _incumbent(cfg)
    sel = runs[-max(1, int(last or 10)):]
    return ok(
        incumbent=(None if inc is None else {
            "id": inc.get("id"), "candidate": inc.get("candidate"),
            "tier": inc.get("tier"), "median_total_s": inc.get("median_total_s"),
            "spills_total": inc.get("spills_total"), "ts": inc.get("ts"),
        }),
        goldens=str(_state(cfg) / "goldens.json"),
        total_runs=len(runs), recent=sel,
    )


if __name__ == "__main__":
    mcp.run()
