"""
fieldtest/results/provenance.py

Judge provenance — which instrument produced a run's scores.

A run that does not record its own judge is not self-describing. Changing
defaults.model and rescoring the same outputs/ directory otherwise produces a
diff indistinguishable from a system regression, which is the same class of
defect fixtures.version already exists to prevent.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from fieldtest.config import Config

FINGERPRINT_LENGTH = 8


def build_judge_block(config: Config) -> dict:
    """
    The judge configuration for a run: defaults, per-eval overrides, fingerprint.

    `overrides` carries only evals whose provider or model differs from defaults,
    and serializes as {} rather than being omitted, so consumers can index it
    unconditionally.
    """
    provider = config.defaults.provider
    model    = config.defaults.model

    overrides: dict[str, dict] = {}
    for uc in config.use_cases:
        for ev in uc.evals:
            if ev.type != "llm":
                continue
            entry = {}
            if ev.provider is not None and ev.provider != provider:
                entry["provider"] = ev.provider
            if ev.model is not None and ev.model != model:
                entry["model"] = ev.model
            if entry:
                overrides[ev.id] = entry

    judge = {
        "provider":    provider,
        "model":       model,
        "temperature": config.defaults.judge_temperature,
        "seed":        config.defaults.judge_seed,
        "overrides":   overrides,
    }
    judge["fingerprint"] = judge_fingerprint(judge)
    return judge


def judge_fingerprint(judge: dict) -> str:
    """
    Stable short hash over the full judge configuration.

    Truncated for readability in `fieldtest history`. Collisions are not a
    safety concern: the fingerprint gates a warning, not a correctness decision.
    """
    payload = {
        "provider":    judge.get("provider"),
        "model":       judge.get("model"),
        "temperature": judge.get("temperature"),
        "seed":        judge.get("seed"),
        "overrides":   judge.get("overrides", {}),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:FINGERPRINT_LENGTH]


def describe_judge_change(current: Optional[dict], baseline: Optional[dict]) -> Optional[str]:
    """
    One line naming which judge fields moved between two runs, or None if they
    match or the comparison cannot be made.
    """
    if not current or not baseline:
        return None
    if current.get("fingerprint") == baseline.get("fingerprint"):
        return None

    changes = []
    for field in ("provider", "model", "temperature", "seed"):
        cur, base = current.get(field), baseline.get(field)
        if cur != base:
            changes.append(f"{field}: {base} → {cur}")

    cur_ov, base_ov = current.get("overrides", {}), baseline.get("overrides", {})
    if cur_ov != base_ov:
        changed = sorted(set(cur_ov) | set(base_ov))
        changes.append(f"per-eval overrides changed ({', '.join(changed)})")

    if not changes:
        changes.append("judge configuration changed")

    return ", ".join(changes)
