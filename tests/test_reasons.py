"""Regression tests for the alert list's plain-English explanation layer.

These exist because of a real bug: Bank of New York Mellon's top-ranked alert read
"deposits already fell 26.3% last quarter" when its deposits had *risen* 26.3%
($332.4B -> $419.7B). The templates hardcoded directional verbs and formatted whatever value
arrived, sign ignored. The same bug printed "losses equal -8.4% of equity" (a double negative)
and described a securities mark that had *improved* by 746% of equity as having "deteriorated".

The explanation layer is the one part of the system no metric checks, which is exactly why it
needs its own tests. The distinguishing words are derived FROM the templates, so a newly added
signal is covered automatically -- there is no per-signal word list to keep in sync.
"""

from __future__ import annotations

import json
import re

import pandas as pd
import pytest

from flightrisk.config import REPORTS
from flightrisk.score import PHRASING, _reason

DIRECTIONAL = {k: v for k, v in PHRASING.items() if isinstance(v, tuple)}


def _pattern(template: str) -> re.Pattern:
    """Turn a phrasing template into a regex that matches its rendered form.

    Order-aware on purpose. A word-set comparison is vacuous for `asset_dep_divergence`, whose
    two branches are the same words reordered ("deposits grew faster than assets" vs "assets
    grew faster than deposits") -- the first version of this test could not tell them apart.
    """
    parts = [re.escape(p) for p in re.split(r"\{[^}]*\}", template)]
    return re.compile(r"-?[\d.,]+\s*%?\s*p?p?".join(parts).replace(r"\ ", r"\s+"), re.IGNORECASE)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _patterns(name: str) -> tuple[re.Pattern, re.Pattern]:
    """(pattern for the negative branch, pattern for the positive branch)."""
    neg, pos = DIRECTIONAL[name]
    return _pattern(neg), _pattern(pos)


def _render(name: str, value: float) -> str:
    return _reason(pd.Series({name: value}), [(name, value)])


def test_there_are_directional_templates_to_check():
    assert len(DIRECTIONAL) >= 10, "expected most change-signals to be sign-aware"


@pytest.mark.parametrize("name", sorted(DIRECTIONAL))
def test_every_directional_template_pair_is_actually_distinguishable(name):
    """Two branches that render identically would make this whole test file vacuous."""
    neg_pat, pos_pat = _patterns(name)
    neg_text, pos_text = _render(name, -0.1234), _render(name, 0.1234)
    assert neg_text != pos_text, f"{name}: both branches render the same text"
    assert not pos_pat.search(neg_text), f"{name}: branches are not distinguishable"


@pytest.mark.parametrize("name", sorted(DIRECTIONAL))
def test_negative_value_uses_negative_wording(name):
    neg_pat, pos_pat = _patterns(name)
    text = _render(name, -0.1234)
    assert neg_pat.search(text), f"{name}: negative branch did not render: {text!r}"
    assert not pos_pat.search(text), f"{name}: positive wording leaked into {text!r}"


@pytest.mark.parametrize("name", sorted(DIRECTIONAL))
def test_positive_value_uses_positive_wording(name):
    neg_pat, pos_pat = _patterns(name)
    text = _render(name, 0.1234)
    assert pos_pat.search(text), f"{name}: positive branch did not render: {text!r}"
    assert not neg_pat.search(text), f"{name}: negative wording leaked into {text!r}"


@pytest.mark.parametrize("name", sorted(DIRECTIONAL))
def test_magnitude_is_rendered_unsigned_so_there_is_no_double_negative(name):
    """A signed magnitude double-negates: "losses equal -8.4% of equity" reads as a gain.

    The sign belongs in the verb, not in the number.
    """
    text = _render(name, -0.084)
    assert "-" not in text.replace("loan-to-deposit", "").replace("noninterest-", ""), text


def test_the_bny_mellon_case_specifically():
    """The exact bug: a +26.3% deposit INFLOW must not be described as a fall."""
    text = _render("dep_growth_1q", 0.263).lower()
    assert "fell" not in text
    assert "jumped" in text and "26.3%" in text
    assert _render("dep_growth_1q", -0.263).lower().count("fell") == 1


def test_every_signal_that_can_appear_in_an_alert_has_phrasing():
    """A signal with no template silently contributes nothing to the reason string."""
    from flightrisk.backtest import structured_features

    missing = [f for f in structured_features() if f not in PHRASING]
    assert not missing, f"signals in the model with no reason-string template: {missing}"


def test_reason_falls_back_rather_than_crashing_on_missing_values():
    assert "Ranked high" in _reason(pd.Series({"dep_growth_1q": None}), [("dep_growth_1q", 0.0)])


# --------------------------------------------------------------------- generated artifact
def _alerts() -> list[dict]:
    path = REPORTS / "alerts_2026Q1.json"
    if not path.exists():
        pytest.skip("run `flightrisk score --quarter 2026Q1` first")
    return json.loads(path.read_text())["alerts"]


def test_generated_alerts_have_wording_consistent_with_their_own_values():
    """End-to-end: every real alert's prose must agree with the values it reports."""
    problems = []
    for a in _alerts():
        for name, value in (a.get("top_signal_values") or {}).items():
            if name not in DIRECTIONAL or value is None:
                continue
            neg_pat, pos_pat = _patterns(name)
            right, wrong = (neg_pat, pos_pat) if value < 0 else (pos_pat, neg_pat)
            if wrong.search(a["reason"]) and not right.search(a["reason"]):
                problems.append(
                    f"{a['bank']}: {name}={value} but the reason uses the opposite direction"
                )
    assert not problems, "reason strings contradict their own values:\n  " + "\n  ".join(problems)


def test_generated_alerts_are_stratified_and_scored():
    alerts = _alerts()
    assert len(alerts) >= 30
    assert {a["size_stratum"] for a in alerts} == {"<$1B", "$1B-$10B", ">$10B"}
    assert all(0.0 <= a["score"] <= 1.0 for a in alerts)
    assert all(a["reason"].startswith(("Flagged because", "Ranked high")) for a in alerts)
