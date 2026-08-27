# Spec 05 — Provider retry parity

**Tier** 1 · **Depends on** spec 02 · **Touches** `providers/*.py`, `results/report.py`, `results/html.py`

## §1 Problem

`AnthropicAdapter` retries HTTP 529 on a six-step backoff schedule, with a comment explaining
the reasoning: the SDK does not auto-retry 529s, so without it a burst of load turns into judge
errors that silently drop out of the pass-rate denominator.

`OpenAIAdapter` and `GeminiAdapter` have no retry at all. Every exception returns
`{"error": str(e)}` immediately.

The aggregation rule compounds this. `build_summary()` excludes error rows from `failure_rate`
entirely and counts them in `error_count`. That exclusion is correct in isolation, but it means
an overloaded provider silently shrinks the sample rather than failing loudly. An eval that
errored on four of five runs reports a rate computed from one observation, and nothing in the
markdown report header says so.

The consequence for spec 08 is direct: comparing judge models across providers is confounded by
which adapter retries. Anthropic completes runs that OpenAI drops, so a judge comparison measures
adapter reliability as much as judge agreement.

## §2 Requirements

1. All three adapters share one retry policy for transient failures.
2. Retryable conditions are identified per provider and documented, not guessed. At minimum:
   429 rate limit, 5xx server errors, 529 overload, and connection or read timeouts.
3. Non-retryable conditions fail immediately as they do now: missing package, missing API key,
   authentication failure, invalid model name, and non-JSON judge response.
4. Retry policy is configurable at the `defaults` level, since a fast local demo and a nightly
   CI run want different patience.
5. The markdown report header and the HTML report show total `error_count` for the run and the
   effective sample reduction, whenever any eval has `error_count > 0`.
6. An eval whose `total_runs` fell below the configured `runs` is visibly marked in the per-eval
   table, not just discoverable by comparing two numbers.

## §3 Contract

Shared policy in `providers/base.py`:

```python
class RetryPolicy(BaseModel):
    max_attempts:  int   = 6
    initial_delay: float = 5.0
    max_delay:     float = 60.0
    multiplier:    float = 2.0


def with_retry(fn: Callable[[], dict], policy: RetryPolicy,
               is_retryable: Callable[[Exception], bool]) -> dict:
    """
    Run fn, retrying on retryable exceptions with capped exponential backoff.
    Returns fn's dict on success, or {"error": str} after exhausting attempts.
    Never raises.
    """
```

Each adapter supplies its own `is_retryable`. The existing Anthropic schedule
`(5, 10, 20, 40, 60, 60)` is reproduced exactly by the defaults above, so the current behavior is
preserved rather than re-tuned.

`config.Defaults` gains `judge_retry: RetryPolicy = RetryPolicy()`.

Report header addition, shown only when errors occurred:

```
Judge errors: 7 of 150 calls failed after retry.
Affected evals: tone_professional (3 of 5 runs scored), no_fabrication (4 of 5 runs scored).
```

## §4 Compatibility

Anthropic behavior is unchanged by construction. OpenAI and Gemini runs that previously errored
under load will now take longer and complete, which changes their reported rates. That is the
intended correction and belongs in the changelog.

No schema change to `-data.json`. `error_count` and `total_runs` already carry the information;
this spec surfaces it.

## §5 Acceptance

Tests in `tests/test_providers.py` and `tests/test_aggregator.py`:

- `test_all_adapters_retry_rate_limit`
- `test_all_adapters_retry_server_error`
- `test_auth_failure_not_retried`
- `test_non_json_response_not_retried`
- `test_retry_policy_configurable`
- `test_anthropic_schedule_unchanged_by_default`
- `test_report_header_shows_error_count_when_nonzero`
- `test_eval_marked_when_total_runs_below_configured`

Behavioral acceptance: force a rate-limit response from each provider under test doubles and
confirm identical attempt counts and identical total elapsed backoff across all three.

## §6 Out of scope

Concurrency tuning. `ThreadPoolExecutor(max_workers=concurrency)` at a default of 5 is a separate
question, though a retry policy that fires constantly is evidence that the default is too high
for a given provider and is worth noting in the docs.
