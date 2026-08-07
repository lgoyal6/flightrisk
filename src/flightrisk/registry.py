"""The signal registry — the single source of truth for candidates and their verdicts.

Every candidate is a self-contained unit declaring a name, a one-line economic rationale, a
compute function that is strictly as-of quarter T, a status, and the evidence attached from
the latest backtest. Scorecards and the README summary table are generated FROM this; they
are never hand-edited. Adding signal #29 is a one-function diff.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from .config import REPORTS


class Status(StrEnum):
    CANDIDATE = "CANDIDATE"
    GRADUATED = "GRADUATED"
    PARKED = "PARKED"
    KILLED = "KILLED"


@dataclass
class Signal:
    name: str
    rationale: str
    compute: Callable[[pd.DataFrame], pd.Series]
    family: str
    status: Status = Status.CANDIDATE
    # A control is registered so it can be tested and argued about explicitly rather than
    # sneaking in as an unexamined confounder (size), or as a deliberate tripwire (dumb).
    is_control: bool = False
    is_dumb: bool = False
    # Constant within a quarter, so it has zero within-quarter ranking power by construction.
    # Registered anyway: the gap between its pooled and within-quarter metrics is the cleanest
    # demonstration of why pooled metrics are inflated on this dataset.
    is_macro: bool = False
    # Whether the signal enters the default structured model. Controls and tripwires are
    # registered so they are TESTED and reported, but they are not modelling features.
    in_model: bool = True
    evidence: dict = field(default_factory=dict)


REGISTRY: dict[str, Signal] = {}


def register(
    name: str,
    rationale: str,
    family: str,
    *,
    is_control: bool = False,
    is_dumb: bool = False,
    is_macro: bool = False,
    in_model: bool = True,
):
    """Decorator registering a compute function as a candidate signal."""

    def wrap(fn: Callable[[pd.DataFrame], pd.Series]) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"duplicate signal name: {name}")
        REGISTRY[name] = Signal(
            name=name,
            rationale=rationale,
            compute=fn,
            family=family,
            is_control=is_control,
            is_dumb=is_dumb,
            is_macro=is_macro,
            in_model=in_model and not (is_dumb or is_macro),
        )
        return fn

    return wrap


def all_signals() -> list[Signal]:
    return list(REGISTRY.values())


def signal_names(include_dumb: bool = True, include_macro: bool = True) -> list[str]:
    return [
        s.name
        for s in REGISTRY.values()
        if (include_dumb or not s.is_dumb) and (include_macro or not s.is_macro)
    ]


def check_integrity() -> None:
    """Registry invariants. Wired into `build` so a malformed entry fails the pipeline."""
    problems = []
    # An empty registry passes every per-signal check vacuously, which is exactly the failure
    # mode that silently emptied the model's feature list earlier: importing `registry` without
    # importing `signals` leaves REGISTRY empty and nothing complains. Fail loudly instead.
    if not REGISTRY:
        raise ValueError(
            "registry is empty -- import flightrisk.signals before calling check_integrity(); "
            "an empty registry makes every feature list silently empty"
        )
    for s in REGISTRY.values():
        if not s.rationale or len(s.rationale.strip()) < 15:
            problems.append(f"{s.name}: rationale missing or too short to be a rationale")
        if not callable(s.compute):
            problems.append(f"{s.name}: compute is not callable")
        if not isinstance(s.status, Status):
            problems.append(f"{s.name}: status is not a Status enum")
        if not s.family:
            problems.append(f"{s.name}: no family")
        if s.is_dumb and s.status is Status.GRADUATED:
            problems.append(
                f"{s.name}: a deliberately-null signal GRADUATED -- the graduation "
                f"framework is broken, this is a tripwire not a result"
            )
    if problems:
        raise ValueError("registry integrity failures:\n  " + "\n  ".join(problems))


def to_frame() -> pd.DataFrame:
    rows = [
        {
            "name": s.name,
            "family": s.family,
            "rationale": s.rationale,
            "status": s.status.value,
            "control": s.is_control,
            "dumb": s.is_dumb,
            "macro": s.is_macro,
            **{f"ev_{k}": v for k, v in s.evidence.items()},
        }
        for s in REGISTRY.values()
    ]
    return pd.DataFrame(rows)


def save_evidence() -> None:
    path = REPORTS / "registry_evidence.json"
    payload = {
        s.name: {"status": s.status.value, "evidence": s.evidence} for s in REGISTRY.values()
    }
    path.write_text(json.dumps(payload, indent=1, default=float))


def load_evidence() -> None:
    """Re-attach evidence from the last backtest so `scorecards` can run standalone."""
    path = REPORTS / "registry_evidence.json"
    if not path.exists():
        return
    payload = json.loads(path.read_text())
    for name, blob in payload.items():
        if name in REGISTRY:
            REGISTRY[name].evidence = blob.get("evidence", {})
            REGISTRY[name].status = Status(blob.get("status", "CANDIDATE"))
