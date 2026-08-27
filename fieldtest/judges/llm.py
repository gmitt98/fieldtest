"""
fieldtest/judges/llm.py

LLM judge — binary and scored variants.
build_binary_judge_prompt() and build_scored_judge_prompt() produce exact,
deterministic prompts per spec §8.
"""
from __future__ import annotations

import threading

from typing import Optional

from fieldtest.config import Config, Eval, ResultRow
from fieldtest.providers import get_provider_adapter
from fieldtest.providers.base import JudgeGenerationConfig


# ---------------------------------------------------------------------------
# Prompt builders — exact content per spec §8
# ---------------------------------------------------------------------------

DELIMITER   = "---"
NEUTRALIZED = "- - -"


def _neutralize_delimiters(output: str) -> tuple[str, bool]:
    """
    Rewrite lines that are exactly the delimiter. Returns (text, was_modified).
    Only whole-line matches are rewritten; `---` inside a line of prose is left alone,
    since it cannot terminate the block.

    The system's output is untrusted data. A bare `---` on its own line closes the
    data block from the judge's perspective, and anything after it reads as
    instruction — which is exactly the input class adversarial fixtures are meant
    to produce.
    """
    lines = output.split("\n")
    if not any(line.strip() == DELIMITER for line in lines):
        # The common case. Returning the original avoids rebuilding a string
        # identical to the one passed in, which matters because this runs once
        # per judge call and judge_runs multiplies that.
        return output, False

    for i, line in enumerate(lines):
        if line.strip() == DELIMITER:
            lines[i] = line.replace(DELIMITER, NEUTRALIZED)
    return "\n".join(lines), True


def _flag_neutralized(detail: Optional[str], was_modified: bool) -> Optional[str]:
    """
    Prefix the judge's reasoning when the output was rewritten, so a user is
    never left wondering why the judge saw something other than the literal text.
    """
    if not was_modified:
        return detail
    return f"[output delimiters neutralized] {detail or ''}".rstrip()


def build_binary_judge_prompt(eval: Eval, output: str) -> str:
    """
    Build binary judge prompt. Two builders reading the spec must produce identical output.

    Template:
        You are evaluating the output of an AI system.

        Eval: {eval.description}

        Pass if: {eval.pass_criteria}
        Fail if: {eval.fail_criteria}
        {examples_block}
        Output to evaluate:
        ---
        {output}
        ---

        Respond with this JSON and nothing else:
        {"answer": "Pass" or "Fail", "reasoning": "one sentence"}
    """
    output, _ = _neutralize_delimiters(output)

    lines = [
        "You are evaluating the output of an AI system.",
        "",
        f"Eval: {eval.description}",
        "",
        f"Pass if: {eval.pass_criteria}",
        f"Fail if: {eval.fail_criteria}",
    ]

    if eval.examples:
        lines.append("")
        lines.append("Examples:")
        for ex in eval.examples:
            lines.append("---")
            lines.append(f"Output: {ex.output}")
            lines.append(f"Label: {ex.label.title()}")
            lines.append(f"Reasoning: {ex.reasoning}")
        lines.append("---")

    lines.extend([
        "",
        "Output to evaluate:",
        "---",
        output,
        "---",
        "",
        "Respond with this JSON and nothing else:",
        '{"answer": "Pass" or "Fail", "reasoning": "one sentence"}',
    ])

    return "\n".join(lines)


def build_scored_judge_prompt(eval: Eval, output: str) -> str:
    """
    Build scored judge prompt.

    Template:
        You are evaluating the output of an AI system.

        Eval: {eval.description}

        Rate the output on a scale from {scale[0]} to {scale[1]}:
        {anchors_block}
        Output to evaluate:
        ---
        {output}
        ---

        Respond with this JSON and nothing else:
        {"score": integer from {scale[0]} to {scale[1]}, "reasoning": "one sentence"}

    Anchors sorted ascending by key.
    """
    scale_min, scale_max = eval.scale[0], eval.scale[1]

    output, _ = _neutralize_delimiters(output)

    lines = [
        "You are evaluating the output of an AI system.",
        "",
        f"Eval: {eval.description}",
        "",
        f"Rate the output on a scale from {scale_min} to {scale_max}:",
    ]

    for key in sorted(eval.anchors.keys()):
        lines.append(f"{key} — {eval.anchors[key]}")

    lines.extend([
        "",
        "Output to evaluate:",
        "---",
        output,
        "---",
        "",
        "Respond with this JSON and nothing else:",
        f'{{"score": integer from {scale_min} to {scale_max}, "reasoning": "one sentence"}}',
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

# Providers silently drop generation parameters they do not support (Anthropic
# has no seed). Collecting the distinct set here — rather than tagging every
# ResultRow — keeps it out of -data.json and lets the report state it once.
_unsupported_lock = threading.Lock()
_unsupported_params: set[str] = set()


def reset_unsupported_params() -> None:
    """Clear the per-run record of dropped judge parameters."""
    with _unsupported_lock:
        _unsupported_params.clear()


def get_unsupported_params() -> list[str]:
    """Distinct 'param (provider)' entries dropped during this run, sorted."""
    with _unsupported_lock:
        return sorted(_unsupported_params)


def call_judge_llm(prompt: str, eval: Eval, config: Config) -> dict:
    """
    Call the judge LLM. Returns parsed JSON dict or {"error": str}.
    Never raises — errors are returned as dict for ResultRow.

    Generation settings come from defaults; parameters the provider dropped are
    recorded for the report header and stripped from the response.
    """
    provider_name = eval.provider or config.defaults.provider
    model         = eval.model    or config.defaults.model

    try:
        adapter = get_provider_adapter(provider_name)
    except Exception as e:
        return {"error": str(e)}

    gen = JudgeGenerationConfig(
        temperature=config.defaults.judge_temperature,
        seed=config.defaults.judge_seed,
    )

    response = adapter.call(model, prompt, gen, config.defaults.judge_retry)

    # Adapters promise a dict and never raising. A third-party adapter that
    # breaks that contract must produce one errored row, not abort the run:
    # this call is inside a ThreadPoolExecutor, and an exception here reaches
    # future.result() and takes every other eval down with it.
    if not isinstance(response, dict):
        return {
            "error": (
                f"Judge adapter for '{provider_name}' returned "
                f"{type(response).__name__}, expected dict"
            )
        }

    dropped = response.pop("unsupported", None)
    if dropped:
        with _unsupported_lock:
            for param in dropped:
                _unsupported_params.add(f"{param} ({provider_name})")

    return response


# ---------------------------------------------------------------------------
# Judge functions
# ---------------------------------------------------------------------------

def judge_llm_binary(
    use_case_id: str, eval: Eval, output: str, fixture: dict, run: int,
    config: Config, judge_run: int = 1,
) -> ResultRow:
    base = dict(
        use_case=use_case_id,
        eval_id=eval.id,
        tag=eval.tag,
        labels=eval.labels,
        type=eval.type,
        fixture_id=fixture["id"],
        run=run,
        judge_run=judge_run,
    )

    output, neutralized = _neutralize_delimiters(output)
    prompt   = build_binary_judge_prompt(eval, output)
    response = call_judge_llm(prompt, eval, config)

    if "error" in response:
        return ResultRow(**base, error=response["error"])

    passed = response.get("answer") == "Pass"
    return ResultRow(
        **base, passed=passed,
        detail=_flag_neutralized(response.get("reasoning"), neutralized),
    )


def judge_llm_scored(
    use_case_id: str, eval: Eval, output: str, fixture: dict, run: int,
    config: Config, judge_run: int = 1,
) -> ResultRow:
    base = dict(
        use_case=use_case_id,
        eval_id=eval.id,
        tag=eval.tag,
        labels=eval.labels,
        type=eval.type,
        fixture_id=fixture["id"],
        run=run,
        judge_run=judge_run,
    )

    output, neutralized = _neutralize_delimiters(output)
    prompt   = build_scored_judge_prompt(eval, output)
    response = call_judge_llm(prompt, eval, config)

    if "error" in response:
        return ResultRow(**base, error=response["error"])

    score     = response.get("score")
    floor_hit = score == eval.scale[0] if score is not None else False
    return ResultRow(
        **base, score=score, floor_hit=floor_hit,
        detail=_flag_neutralized(response.get("reasoning"), neutralized),
    )
