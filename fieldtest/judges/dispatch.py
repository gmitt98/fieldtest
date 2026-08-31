"""
fieldtest/judges/dispatch.py

dispatch_judge() — routes to correct judge by eval.type.
Never raises (except ConfigError for hard config errors).
All other failures go into ResultRow.error.
"""
from __future__ import annotations

from fieldtest.config import Config, Eval, ResultRow
from fieldtest.errors import ConfigError
from fieldtest.judges.llm import judge_llm_binary, judge_llm_scored
from fieldtest.judges.reference import judge_reference
from fieldtest.judges.regex_ import judge_regex
from fieldtest.judges.registry import get_rule


def _unusable_rule(eval_id: str, result: object) -> str:
    """Error text for a rule return that carries no usable boolean verdict."""
    try:
        got = repr(result)
    except Exception:  # pragma: no cover - a __repr__ that raises
        got = f"<unreprable {type(result).__name__}>"
    return (
        f"rule '{eval_id}' returned no usable verdict "
        f'(wanted {{"passed": bool, "detail": str}}); got {got[:200]}'
    )


def dispatch_judge(
    use_case_id: str,
    eval: Eval,
    output: str,
    fixture: dict,
    run: int,
    config: Config,
    judge_run: int = 1,
) -> ResultRow:
    """
    Route to the correct judge. Returns a ResultRow always.

    Raises ConfigError only for hard config problems (missing rule registration,
    unknown eval type) — these abort the run.

    All other failures (API errors, parse errors) → ResultRow(error=message).
    """
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

    eval_type = eval.type

    if eval_type == "rule":
        fn = get_rule(eval.id)
        if fn is None:
            raise ConfigError(
                f"No rule registered for eval '{eval.id}'. "
                f"Add @rule('{eval.id}') to evals/rules.py"
            )
        try:
            result = fn(output, fixture.get("inputs", {}))
            # A rule that returns no usable verdict is a rule failure, not a
            # passing output. `passed=result.get("passed")` made every
            # non-conforming shape — a misspelled key, a branch that forgets to
            # return, a dict of some other shape — arrive as passed=None with
            # error=None, which lands in the denominator but is never counted a
            # failure: the headline read 100% pass with a confidence interval
            # and zero errors while Tag Health on the same rows read 0%. The LLM
            # branch already guards this class (llm.py); the rule branch did not,
            # and it failed in the more dangerous direction. The contract is
            # documented as {"passed": bool, "detail": str}.
            verdict = result.get("passed") if isinstance(result, dict) else None
            if not isinstance(verdict, bool):
                return ResultRow(**base, error=_unusable_rule(eval.id, result))
            return ResultRow(
                **base,
                passed=verdict,
                detail=result.get("detail"),
            )
        except ConfigError:
            raise
        except Exception as e:
            return ResultRow(**base, error=str(e))

    elif eval_type == "regex":
        return judge_regex(use_case_id, eval, output, fixture, run)

    elif eval_type == "llm":
        if eval.binary:
            return judge_llm_binary(
                use_case_id, eval, output, fixture, run, config, judge_run
            )
        else:
            return judge_llm_scored(
                use_case_id, eval, output, fixture, run, config, judge_run
            )

    elif eval_type == "reference":
        return judge_reference(use_case_id, eval, output, fixture, run)

    else:
        # v2 extension point — raise clearly rather than silently skip
        raise ConfigError(
            f"Unknown eval type '{eval_type}'. Valid types: rule | regex | llm | reference"
        )
