"""
fieldtest/judges/llm.py

LLM judge — binary and scored variants.
build_binary_judge_prompt() and build_scored_judge_prompt() produce exact,
deterministic prompts per spec §8.
"""
from __future__ import annotations

from fieldtest.config import Config, Eval, ResultRow
from fieldtest.providers import get_provider_adapter


# ---------------------------------------------------------------------------
# Prompt builders — exact content per spec §8
# ---------------------------------------------------------------------------

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

def call_judge_llm(prompt: str, eval: Eval, config: Config) -> dict:
    """
    Call the judge LLM. Returns parsed JSON dict or {"error": str}.
    Never raises — errors are returned as dict for ResultRow.
    """
    provider_name = eval.provider or config.defaults.provider
    model         = eval.model    or config.defaults.model

    try:
        adapter = get_provider_adapter(provider_name)
    except Exception as e:
        return {"error": str(e)}

    return adapter.call(model, prompt)


# ---------------------------------------------------------------------------
# Judge functions
# ---------------------------------------------------------------------------

def judge_llm_binary(
    use_case_id: str, eval: Eval, output: str, fixture: dict, run: int, config: Config
) -> ResultRow:
    base = dict(
        use_case=use_case_id,
        eval_id=eval.id,
        tag=eval.tag,
        labels=eval.labels,
        type=eval.type,
        fixture_id=fixture["id"],
        run=run,
    )

    prompt   = build_binary_judge_prompt(eval, output)
    response = call_judge_llm(prompt, eval, config)

    if "error" in response:
        return ResultRow(**base, error=response["error"])

    passed = response.get("answer") == "Pass"
    return ResultRow(**base, passed=passed, detail=response.get("reasoning"))


def judge_llm_scored(
    use_case_id: str, eval: Eval, output: str, fixture: dict, run: int, config: Config
) -> ResultRow:
    base = dict(
        use_case=use_case_id,
        eval_id=eval.id,
        tag=eval.tag,
        labels=eval.labels,
        type=eval.type,
        fixture_id=fixture["id"],
        run=run,
    )

    prompt   = build_scored_judge_prompt(eval, output)
    response = call_judge_llm(prompt, eval, config)

    if "error" in response:
        return ResultRow(**base, error=response["error"])

    score     = response.get("score")
    floor_hit = score == eval.scale[0] if score is not None else False
    return ResultRow(**base, score=score, floor_hit=floor_hit, detail=response.get("reasoning"))
