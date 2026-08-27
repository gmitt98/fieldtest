# Spec 12 — Verification tiers

**Tier** 2 · **Depends on** 11 · **Touches** `tests/`, `pyproject.toml`, CI workflow · **Status** draft

## §1 Problem

The v2 work shipped with 308 passing tests and four defects that none of them could reach:

| Defect | Why the suite missed it |
|---|---|
| `judge_run` never reached LLM rows | Tests built `ResultRow`s directly; nothing flowed a task through `dispatch_judge` |
| `fieldtest calibrate` crashed on any `rule` eval | Every calibration test patched `runner.score`, so the real scoring path never ran |
| `temperature` rejected by Sonnet 5 / Opus 5 | The provider's contract changed; the mock's did not |
| `max_tokens` rejected by OpenAI reasoning models | Same |

The first two are integration gaps and need no network. The last two are contract drift: the
adapter tests assert `temperature` and `max_tokens` are forwarded, which is *correct* — the API
rejecting them is a fact about the provider that no local assertion can discover.

The pattern is worth naming, because it is the same one fieldtest exists to point at. A judge that
agrees with itself proves nothing without ground truth; a mock that agrees with the code that
built it proves nothing without contact with the real provider. fieldtest asks its users to
measure their instrument, and its own test suite was measuring itself.

## §2 Requirements

1. Three tiers, distinguished by what they touch: unit (no I/O), integration (real code paths, no
   network), live (real provider).
2. Unit and integration run in CI on every push, and remain the default `pytest` invocation.
3. Live tests never run by accident. Absent credentials they skip with a reason, not a failure.
4. Integration tests exercise `runner.score()`, `dispatch_judge()` and the aggregation path
   end-to-end against a registered fake provider — through the spec 11 registration mechanism, not
   a patch. A test that patches `runner.score` cannot catch a bug inside `runner.score`.
5. Live tests assert the *provider contract*, not fieldtest's logic: that a judge call returns
   parseable JSON, that a pinned temperature is accepted or rejected as documented, that the
   documented error strings still match `rejects_parameter()`.
6. Live coverage spans providers, and one credential should reach several. Requiring four accounts
   to test four adapters means three of them go untested.
7. Documented provider error strings live in one fixture file with a source link and a date per
   entry, so drift is visible in review rather than discovered in production.

## §3 Contract

Markers in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: real code paths, no network",
    "live: requires provider credentials; skipped without them",
]
addopts = "-m 'not live'"
```

`pytest` runs unit + integration. `pytest -m live` runs the live tier. CI runs the default on
every push and the live tier on a schedule, where a failure means a provider changed something,
not that a contributor did.

Live tests skip on a missing key, per provider:

```python
requires = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="live tier: set OPENROUTER_API_KEY",
)
```

**OpenRouter is the primary live credential.** One key reaches Anthropic, OpenAI, Google, xAI and
open-weight models behind `vendor/model` slugs, which turns "we have never made a real OpenAI or
Gemini call" from a four-account problem into a one-key problem. Provider-native keys remain
supported for testing the bespoke Anthropic and Gemini adapters, which OpenRouter does not
exercise — it reaches those models through the OpenAI protocol, not through those adapters.

Documented rejections, versioned as data rather than as assertions scattered through tests:

```python
# tests/fixtures/provider_errors.py
REJECTIONS = [
    Rejection(
        provider="openai",
        param="temperature",
        message="Unsupported parameter: 'temperature' is not supported with this model.",
        source="https://platform.openai.com/docs/guides/reasoning",
        confirmed="2026-08-26",
    ),
    ...
]
```

One test asserts `rejects_parameter()` matches every entry; another asserts it does *not* match a
list of unrelated 400s. When a provider rephrases, the fixture is where the fix goes.

## §4 Compatibility

Purely additive. The default `pytest` invocation gains integration tests and gains nothing that
needs network or credentials, so contributors and CI are unaffected.

## §5 Acceptance

- `test_default_pytest_run_excludes_live_tier`
- `test_live_tests_skip_without_credentials`
- `test_score_end_to_end_through_a_registered_provider` (integration)
- `test_calibrate_end_to_end_through_a_registered_provider` (integration)
- `test_judge_run_survives_the_full_dispatch_path` (integration)
- `test_rule_evals_resolve_through_every_entry_point` (integration)
- `test_rejects_parameter_matches_every_documented_rejection`
- `test_rejects_parameter_ignores_unrelated_bad_requests`
- `test_live_judge_returns_parseable_json` (live, per provider)
- `test_live_pinned_temperature_is_accepted_or_reported` (live, per provider)
- `test_live_openrouter_reaches_a_model_from_each_lab` (live)

Behavioral acceptance: re-introduce each of the four defects above, one at a time, and confirm the
suite fails. A tier that cannot catch the bug it was built for is decoration. The two integration
gaps must fail under `pytest`; the two contract gaps must fail under `pytest -m live` and, for the
error strings, under the fixture test without any network at all.

## §6 Out of scope

Recording and replaying real responses (VCR-style). It would make contract drift *less* visible by
freezing yesterday's contract into a fixture that keeps passing — which is the failure this spec
exists to correct, wearing a costume.

Also out of scope: gating merges on the live tier. Provider outages are not contributor errors,
and a required check that fails for reasons outside the repository teaches people to ignore it.
