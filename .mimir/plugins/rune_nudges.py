"""RUNE nudge plugins — the methodology's advisory habits, keyed off the ledger.

Copy this into your plugins directory to activate it — `$MIMIR_PLUGINS_DIR` or
`<workspace>/.mimir/plugins/`. Each rule reads the RUNE ratchet ledger
(<state_dir>/ledger.json, written by the RUNE server tools; the state_dir is
named by the workspace's rune.json) and reminds — never blocks. Both rules are
toggleable via `/nudges` and are silent without a RUNE project in the workspace.

- rune_backport_owed: an accepted T1/T2 machine-code edit whose backport outcome
  is still unrecorded ("none"). The backport acceptance rule: ship the language
  spelling when it reproduces the gain within noise; record the attempt even
  when it fails — that record is what justifies a T2 artifact.
- rune_rejection_record: a rejected candidate whose ledger note carries no
  numbers. Rejections are evidence; the next sweep cites them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mimir.client.extensions import NudgeRule, register_nudge

try:  # canonical root, same import the client uses
    from mimir.config.constants import WORKSPACE_ROOT
except Exception:  # pragma: no cover
    WORKSPACE_ROOT = os.path.abspath(os.getcwd())


def _ledger() -> "list[dict] | None":
    """Return the RUNE ledger of the workspace, or None when there is no RUNE project."""
    cfg_p = Path(WORKSPACE_ROOT) / "rune.json"
    try:
        if not cfg_p.is_file():
            return None
        cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
        state = cfg.get("state_dir")
        if not state:
            return None
        ledger_p = Path(str(state)).expanduser() / "ledger.json"
        if not ledger_p.is_file():
            return None
        data = json.loads(ledger_p.read_text(encoding="utf-8"))
        runs = data.get("runs", []) if isinstance(data, dict) else []
        return runs if isinstance(runs, list) else None
    except Exception:
        return None  # a nudge must never break on a malformed project file


def _backport_owed(agent: Any, query: str, active_mode: str, execution_context: dict) -> bool:
    runs = _ledger()
    if not runs:
        return False
    for run in reversed(runs):
        if not isinstance(run, dict):
            continue
        if run.get("tier") in ("T1", "T2") and run.get("decision") == "accepted":
            # newest accepted machine-code edit decides; anything older is done
            return run.get("backport", "none") == "none"
    return False


def _backport_render(agent: Any, execution_context: dict) -> str:
    return (
        "An accepted T1/T2 machine-code edit has no recorded backport outcome. "
        "RUNE requires the attempt: express the same transformation in the source "
        "language and gate the backport — adopt the language spelling when it "
        "reproduces the gain within replicate spread; otherwise record the backport "
        "as 'lossy' with the failed attempt's numbers (that record is what justifies "
        "shipping a binary patch). rune_mark records the outcome."
    )


def _rejection_unrecorded(agent: Any, query: str, active_mode: str, execution_context: dict) -> bool:
    runs = _ledger()
    if not runs:
        return False
    for run in reversed(runs):
        if not isinstance(run, dict):
            continue
        if run.get("decision") == "rejected":
            # fire only while the newest rejection carries no numbers
            note = str(run.get("note") or "")
            return not any(ch.isdigit() for ch in note)
        if run.get("decision") == "accepted":
            return False
    return False


def _rejection_render(agent: Any, execution_context: dict) -> str:
    return (
        "The newest rejected candidate has no measured numbers in its ledger note. "
        "A rejection without its numbers cannot teach the next sweep — record the "
        "gate results (failed gate, medians, delta) via rune_mark so the operating "
        "rule can cite it."
    )


def register() -> None:
    """Register this plugin's descriptors (called for its import side effect)."""
    register_nudge(NudgeRule(
        name="rune_backport_owed",
        layer="guidance",
        predicate=_backport_owed,
        render=_backport_render,
        tiers=frozenset({("strict", "agent"), ("strict", "plan"),
                         ("light", "agent"), ("light", "plan")}),
    ))
    register_nudge(NudgeRule(
        name="rune_rejection_record",
        layer="guidance",
        predicate=_rejection_unrecorded,
        render=_rejection_render,
        tiers=frozenset({("strict", "agent"), ("strict", "plan"),
                         ("light", "agent"), ("light", "plan")}),
    ))


register()
