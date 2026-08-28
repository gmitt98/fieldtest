# Spec 12 — Verification tiers

**Tier** 2 · **Depends on** 11 · **Touches** `tests/`, `pyproject.toml`, CI workflow · **Status** shipped

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

The clearest demonstration arrived after this spec was first drafted, and it is sharper than the
one originally written here.

Spec 02's provider matrix recorded `gemini | seed | no`, and the adapter enforced that entry
directly:

```python
unsupported = ["seed"] if gen.seed is not None else []
```

The parameter was never sent. The refusal was manufactured locally and reported to the user in the
same field a provider refusal would occupy — `⚠ judge parameters ignored by provider: seed
(gemini)`. A probe run against that adapter returned `unsupported: ['seed']`, which was then read
as evidence that Gemini rejects the parameter, and the table was updated to say so.

It was fieldtest reporting its own assumption back, in the voice of the provider. Only a call that
actually sent the parameter settled it, and `gemini-3.7-flash` accepts it.

A wrong table is a documentation bug. A wrong table whose implementation fabricates confirming
evidence is the failure this spec exists to catch, and it survived a mock suite, three review
passes and a hand-run probe. Nothing but a real call could have caught it, because every other
source in the loop was downstream of the same assumption.

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
5b. A live test picks its model by discovery, and treats discovery as a candidate list rather
   than a guarantee. Google's `models.list()` returned `gemini-2.5-flash`; calling it returned
   `404 — no longer available to new users`. A tier that hardcodes a model id goes stale, and one
   that trusts the list fails on the provider's schedule, so it must walk candidates newest-first
   and tolerate a 404 by trying the next.
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

`pytest` runs unit + integration. `pytest -m live` runs the live tier.

`.github/workflows/test.yml` runs the default tier plus `scripts/verify_tiers.py` on every push
and pull request, and the live tier on a daily schedule or on demand, where a failure means a
provider changed something rather than that a contributor did. The live job needs four repository
secrets — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` — and
fails rather than passing quietly if none are present.

Live tests skip on a missing key, per provider:

```python
requires = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="live tier: set OPENROUTER_API_KEY",
)
```

**A silent skip is the same blind spot in a quieter form.** `2 skipped` scrolls past and the run
reads as verification, which is how the four defects reached a release in the first place — a
suite that could not ask, reporting success. So the run ends with a coverage report naming every
provider that went untested, and a live run with no credentials at all exits non-zero rather than
green:

```
============================== live tier coverage ==============================
  exercised   openai_compatible  OPENROUTER_API_KEY
  NOT TESTED  anthropic          ANTHROPIC_API_KEY is unset

  1 of 4 providers went untested. Their adapters are unverified against a real API by this run.
```

Per-provider skipping stays allowed, because nobody holds accounts everywhere. Skipping without
saying so does not.

`openai_compatible` is a protocol, not a vendor, so its live tests take the endpoint from
`FIELDTEST_LIVE_BASE_URL` and `FIELDTEST_LIVE_MODEL` and default to OpenRouter. A contributor with
a local vLLM or Ollama can exercise the same path without an account anywhere.

**OpenRouter is the primary live credential, and it is not sufficient on its own.** One key
reaches Anthropic, OpenAI, Google, xAI and open-weight models behind `vendor/model` slugs, which
turns "we have never made a real OpenAI or Gemini call" from a four-account problem into a one-key
problem for the happy path.

But it cannot exercise the parameter-rejection path, and that was established empirically rather
than assumed. Calling `openai/o3-mini` through OpenRouter with `temperature: 0.0` and `max_tokens`
set — the exact combination OpenAI documents as a 400 on reasoning models — **succeeded, with
nothing reported as dropped**. Whether OpenRouter strips the parameter or that model simply
accepts it is untested — o3-mini has never been called natively from here — and OpenRouter's docs
describe what happens when a parameter is *absent* while saying nothing about an unsupported one
being *present*. The mechanism does not matter for this spec's purpose; the observation does.

That is good for users and bad for tests. `rejects_parameter()` and `call_dropping_unsupported()`
exist precisely for the case OpenRouter hides, so the live tier needs **provider-native keys** to
reach it:

| Path | OpenRouter | Native key |
|---|---|---|
| Adapter works, JSON parses | yes | yes |
| Cross-lab and open-weight coverage | yes | no (one lab each) |
| Parameter rejection drops and reports | **no — did not fire** | yes |
| Bespoke Anthropic / Gemini adapters | no — OpenAI protocol only | yes |

A live tier that only ever runs through OpenRouter would report green while leaving the newest,
least-tested code in the adapters completely unexercised — the same shape of false confidence this
spec was written to correct.

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
- `test_live_unsupported_parameter_is_dropped_and_named` (live, native keys only)
- `test_live_model_discovery_tolerates_an_unavailable_candidate` (live)

The first of those two is the one the tier exists for. Verified by hand before this spec was
written: `gpt-5` returns `unsupported: ["temperature"]` and completes, which also proves
`max_tokens` was renamed to `max_completion_tokens`, since the call could not otherwise have
succeeded. `gemini-3.7-flash` returns `unsupported: ["seed"]` and completes. Both paths were
written from documentation and neither had ever been triggered by a provider.

Behavioral acceptance: re-introduce each of the four defects above, one at a time, and confirm the
suite fails. A tier that cannot catch the bug it was built for is decoration. The two integration
gaps must fail under `pytest`; the two contract gaps must fail under `pytest -m live` and, for the
error strings, under the fixture test without any network at all.

## §6 Out of scope

Recording and replaying real responses (VCR-style). It would make contract drift *less* visible by
freezing yesterday's contract into a fixture that keeps passing. That is the failure this spec
exists to correct, not a way to avoid it.

Also out of scope: gating merges on the live tier. Provider outages are not contributor errors,
and a required check that fails for reasons outside the repository teaches people to ignore it.
