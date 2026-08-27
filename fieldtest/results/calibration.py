"""
fieldtest/results/calibration.py

Agreement statistics for a judge panel.

Kappa rather than raw agreement is the point. On a `safe` eval where the true
failure rate is 5%, two judges that both always answer pass show 95% raw
agreement and a kappa near zero. Raw agreement alone would certify a useless
judge.
"""
from __future__ import annotations

import math
from typing import Optional

# Verdict key: (fixture_id, run) → the output that was judged.
Verdicts = dict[tuple[str, int], bool]
Scores   = dict[tuple[str, int], float]


# ---------------------------------------------------------------------------
# Binary statistics
# ---------------------------------------------------------------------------

def raw_agreement(a: Verdicts, b: Verdicts) -> Optional[float]:
    """Fraction of shared outputs two judges ruled the same way."""
    shared = set(a) & set(b)
    if not shared:
        return None
    return round(sum(1 for k in shared if a[k] == b[k]) / len(shared), 6)


def cohens_kappa(a: Verdicts, b: Verdicts) -> Optional[float]:
    """
    Chance-corrected agreement between two judges.

    Returns None when there is nothing to compare. When both judges answered
    identically for every output and only ever used one category, expected
    agreement is 1 and kappa is undefined; that degenerate case returns 0.0,
    since a judge that never varies has demonstrated no skill.
    """
    shared = sorted(set(a) & set(b))
    if not shared:
        return None

    n  = len(shared)
    po = sum(1 for k in shared if a[k] == b[k]) / n

    pe = 0.0
    for category in (True, False):
        pa = sum(1 for k in shared if a[k] is category) / n
        pb = sum(1 for k in shared if b[k] is category) / n
        pe += pa * pb

    if pe >= 1.0:
        return 0.0
    return round((po - pe) / (1 - pe), 6)


def fleiss_kappa(judges: list[Verdicts]) -> Optional[float]:
    """
    Chance-corrected agreement across the whole panel.

    Requires at least two judges and one output every judge ruled on.
    """
    if len(judges) < 2:
        return None

    shared = set(judges[0])
    for j in judges[1:]:
        shared &= set(j)
    if not shared:
        return None

    n = len(judges)
    if n < 2:
        return None

    items = sorted(shared)
    # counts[i] = (raters saying pass, raters saying fail) for output i
    counts = [
        (
            sum(1 for j in judges if j[item] is True),
            sum(1 for j in judges if j[item] is False),
        )
        for item in items
    ]

    p_bar = sum(
        (passes**2 + fails**2 - n) / (n * (n - 1)) for passes, fails in counts
    ) / len(counts)

    total = len(counts) * n
    p_pass = sum(passes for passes, _ in counts) / total
    p_fail = sum(fails for _, fails in counts) / total
    p_e    = p_pass**2 + p_fail**2

    if p_e >= 1.0:
        return 0.0
    return round((p_bar - p_e) / (1 - p_e), 6)


# ---------------------------------------------------------------------------
# Scored statistics
# ---------------------------------------------------------------------------

def mean_absolute_deviation(a: Scores, b: Scores) -> Optional[float]:
    """Mean |difference| over the outputs both sides scored."""
    shared = set(a) & set(b)
    if not shared:
        return None
    return round(sum(abs(a[k] - b[k]) for k in shared) / len(shared), 4)


def signed_bias(judge: Scores, human: Scores) -> Optional[float]:
    """
    Mean signed difference. Positive means the judge scores higher than the
    human — a lenient judge, which a symmetric deviation figure would hide.
    """
    shared = set(judge) & set(human)
    if not shared:
        return None
    return round(sum(judge[k] - human[k] for k in shared) / len(shared), 4)


def _ranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(a: Scores, b: Scores) -> Optional[float]:
    """
    Rank correlation between two judges' scores.

    Pearson over ranks, so ties are handled correctly. Returns None when fewer
    than two shared outputs, or when either side is constant and no ranking
    exists to correlate.
    """
    shared = sorted(set(a) & set(b))
    if len(shared) < 2:
        return None

    ra = _ranks([a[k] for k in shared])
    rb = _ranks([b[k] for k in shared])

    n  = len(shared)
    ma = sum(ra) / n
    mb = sum(rb) / n

    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va  = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb  = math.sqrt(sum((y - mb) ** 2 for y in rb))

    if va == 0 or vb == 0:
        return None
    return round(cov / (va * vb), 6)
