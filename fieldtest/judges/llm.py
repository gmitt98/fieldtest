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


def _render_inputs(inputs: Optional[dict]) -> list[str]:
    """
    The fixture's inputs as prompt lines, or nothing when there are none.

    Keys are sorted because YAML preserves file order and prompt bytes must not
    depend on how someone happened to type a fixture. Values are neutralized the
    same way outputs are (spec 03): a fixture is as able to carry an injection as
    an output, and adversarial fixtures are a documented use case, so the more
    likely of the two.
    """
    if not inputs:
        return []

    lines = ["System input:", DELIMITER]
    for key in sorted(inputs):
        value, _ = _neutralize_delimiters(str(inputs[key]))
        if "\n" in value:
            lines.append(f"{key}:")
            lines.extend(f"  {line}" for line in value.split("\n"))
        else:
            lines.append(f"{key}: {value}")
    lines.extend([DELIMITER, ""])
    return lines


def _flag_neutralized(detail: Optional[str], was_modified: bool) -> Optional[str]:
    """
    Prefix the judge's reasoning when the output was rewritten, so a user is
    never left wondering why the judge saw something other than the literal text.
    """
    if not was_modified:
        return detail
    return f"[output delimiters neutralized] {detail or ''}".rstrip()


def build_binary_judge_prompt(
    eval: Eval, output: str, inputs: Optional[dict] = None
) -> str:
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

    lines.append("")
    # Before the output: the judge reads the question before the answer, which
    # is the order the task is stated in.
    lines.extend(_render_inputs(inputs if eval.judge_sees_inputs else None))

    lines.extend([
        "Output to evaluate:",
        "---",
        output,
        "---",
        "",
        "Respond with this JSON and nothing else:",
        '{"answer": "Pass" or "Fail", "reasoning": "one sentence"}',
    ])

    return "\n".join(lines)


def build_scored_judge_prompt(
    eval: Eval, output: str, inputs: Optional[dict] = None
) -> str:
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

    lines.append("")
    lines.extend(_render_inputs(inputs if eval.judge_sees_inputs else None))

    lines.extend([
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
        adapter = get_provider_adapter(
            provider_name,
            config.providers.get(provider_name),
        )
    except Exception as e:
        return {"error": str(e)}

    gen = JudgeGenerationConfig(
        temperature=config.defaults.judge_temperature,
        seed=config.defaults.judge_seed,
    )

    # Adapters promise a dict and never raising. A third-party adapter that
    # breaks that contract must produce one errored row, not abort the run:
    # this call is inside a ThreadPoolExecutor, and an exception here reaches
    # future.result() and takes every other eval down with it.
    #
    # The isinstance guard below was written for that risk but only covered half
    # of it. Every built-in adapter catches its own exceptions, so nothing
    # exercised the raising half until @provider made third-party adapters a
    # supported thing.
    try:
        response = adapter.call(model, prompt, gen, config.defaults.judge_retry)
    except Exception as e:
        return {
            "error": (
                f"Judge adapter for '{provider_name}' raised "
                f"{type(e).__name__}: {e}"
            )
        }

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


def _unusable(response: dict, wanted: str) -> str:
    """Error text for a judge reply that parsed as JSON but says nothing usable."""
    import json

    try:
        got = json.dumps(response)
    except (TypeError, ValueError):
        got = repr(response)
    return f"judge returned no usable verdict (wanted {wanted}); got {got[:200]}"


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
    prompt   = build_binary_judge_prompt(eval, output, fixture.get("inputs"))
    response = call_judge_llm(prompt, eval, config)

    if "error" in response:
        return ResultRow(**base, error=response["error"])

    # A response carrying no usable verdict is a judge failure, not a failing
    # output. `response.get("answer") == "Pass"` made every other shape — a
    # lowercase "pass", a different key, a custom @provider returning its own
    # dict — read as Fail, so the report showed 0% with a confidence interval
    # and zero errors. A wrong number stated confidently is the one thing this
    # tool must not do.
    answer  = response.get("answer")
    verdict = answer.strip().lower() if isinstance(answer, str) else None
    if verdict not in ("pass", "fail"):
        return ResultRow(**base, error=_unusable(response, 'answer: "Pass" or "Fail"'))

    return ResultRow(
        **base, passed=verdict == "pass",
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
    prompt   = build_scored_judge_prompt(eval, output, fixture.get("inputs"))
    response = call_judge_llm(prompt, eval, config)

    if "error" in response:
        return ResultRow(**base, error=response["error"])

    score = response.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return ResultRow(**base, error=_unusable(response, "score: a number"))

    lo, hi = eval.scale
    if not lo <= score <= hi:
        # Silently averaging a 9 on a 1–5 scale moves the mean and hides that
        # the judge ignored the scale it was given.
        return ResultRow(
            **base,
            error=f"judge returned score {score}, outside the {lo}-{hi} scale",
        )

    return ResultRow(
        **base, score=score, floor_hit=score == lo,
        detail=_flag_neutralized(response.get("reasoning"), neutralized),
    )
