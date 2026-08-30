# Spec 11 — Provider surface beyond the big three

**Tier** 2 · **Depends on** 02, 05 · **Touches** `providers/`, `config.py`, README · **Status** shipped · §1 rewritten after live verification

## §1 Problem

An earlier draft of this spec opened by claiming that a user who judges with Grok, with a model on
Together or Fireworks, or with an open-weight model served through vLLM or Ollama "cannot use
fieldtest at all." Live verification disproved it before any code was written. The `openai` SDK
honours `OPENAI_BASE_URL`, so fieldtest's existing OpenAI adapter already reaches any endpoint
speaking the chat-completions protocol. `openai/gpt-4o-mini`, `openai/o3-mini` and
`meta-llama/llama-3.3-70b-instruct` returned parseable verdicts through OpenRouter with no code
change. An open-weight model judged a fixture set through an adapter written for OpenAI.

The problem is smaller than that draft claimed, and it is in a different place. Two parts remain.

**The endpoint is invisible to the config.** `base_url` can only be set in the environment, which
means it is not versioned with the rest of `config.yaml`, not visible to a reader of the config,
and not in the judge fingerprint from spec 01. Two runs against `llama-3.3-70b-instruct` on
different endpoints produce identical fingerprints today, so `find_baseline()` will compare them
as though one instrument produced both. A shell variable is deciding which model judged your
`safe` evals, and nothing in the results records the decision. That is a provenance defect, not
an access defect, and spec 01 exists to prevent exactly this class of thing.

**Anything not speaking the OpenAI protocol has no path.** Here the original framing holds: there
is no supported way to name a judge fieldtest does not ship an adapter for, short of forking. That
matters more than it would in a tool that ran the system under test. The
generator-writes-files contract means fieldtest needs a provider for one thing only — the judge —
and it cuts against the README's own position, which is that the taxonomy is fieldtest's
contribution and the runtime belongs to whoever already has one.

So layer 1 below is ergonomics and provenance for a path that already works, and layer 1c is the
capability that is genuinely absent. They are specified together because they share a config
block, not because they solve the same problem.

The supporting machinery is already built and already proven. Live verification found that
Anthropic removed sampling parameters on its 5-series and that OpenAI's reasoning models reject
`temperature` and rename `max_tokens`. Both are absorbed by `call_dropping_unsupported()` in
`providers/base.py`, which is provider-agnostic by construction. That is what makes a fourth
adapter cheap rather than a fourth maintenance burden.

## §2 Requirements

The surface has three layers, in descending order of how many users each serves and ascending
order of how much work each asks of them.

1. A user can point fieldtest at any endpoint that speaks the OpenAI chat-completions protocol,
   without fieldtest shipping a named adapter for it. This layer covers OpenRouter, vLLM, Ollama,
   Together, Fireworks, and xAI — most of the gap, with one adapter.
1b. **OpenRouter is called out by name in the docs**, because it collapses the long tail into one
   credential: a single key and base URL reach Anthropic, OpenAI, Google, xAI, Meta, Qwen,
   DeepSeek and Mistral models behind `vendor/model` slugs. A user who wants to calibrate a panel
   across four labs should not need four accounts to do it.

   Verified before this spec was written, not after: fieldtest's existing OpenAI adapter reaches
   OpenRouter with no code change at all, because the `openai` SDK honours `OPENAI_BASE_URL`.
   `openai/gpt-4o-mini`, `openai/o3-mini` and `meta-llama/llama-3.3-70b-instruct` all returned
   parseable verdicts through it, with `seed` accepted. The premise of layer 1 is therefore
   demonstrated rather than assumed — an `openai_compatible` adapter is mostly a way to configure
   `base_url` from `config.yaml` instead of the environment.

   One caveat belongs in the docs beside the recommendation, stated as narrowly as the evidence
   allows. `openai/o3-mini` through OpenRouter took `temperature` and `max_tokens` without
   complaint, and fieldtest reported nothing dropped. Called natively, `gpt-5` returns
   `unsupported: ["temperature"]` through the same adapter.

   Those are different models, so this does not establish that OpenRouter strips parameters —
   o3-mini has never been called natively from here, and the difference could as easily be the
   model. What it does establish is the part that matters operationally: **the drop path did not
   fire through OpenRouter**, so a judge reached that way may be running unpinned with nothing in
   the report to reveal it, and the live tier cannot use OpenRouter to exercise that path. See
   spec 12.
1c. **A user can register their own adapter** for anything that does not speak that protocol,
   without forking fieldtest. This mirrors `@rule`, which already loads user code from
   `evals/rules.py` — the precedent for user-supplied behavior in a project directory exists and
   should not be reinvented.
2. Configuration is per use case in `config.yaml`, versioned with everything else, consistent
   with how `defaults.provider` and the calibration panel already work.
3. Authentication is by environment variable name, never by literal key in config. The variable
   name is config; the value never is.
4. A self-hosted endpoint may need no key at all. Absence of a key is a valid configuration, not
   an error.
5. Base URL is required for this provider type. There is no default, because guessing one would
   silently send a user's outputs somewhere they did not name.
6. Parameter support is discovered, not declared. An endpoint that rejects a generation parameter
   is handled by the existing drop-and-rename path and reported in the header, exactly as the
   three named providers are.
7. The retry policy from spec 05 applies unchanged. A self-hosted endpoint is more likely to be
   briefly unavailable than a hosted one, not less.
8. `fieldtest validate` reports which providers a config references and whether the environment
   variable each one names is set — before a run, not after twenty errored rows.

## §3 Contract

Config addition:

```yaml
defaults:
  provider: openai_compatible
  model: llama-3.3-70b-instruct

providers:
  openai_compatible:
    base_url: http://localhost:8000/v1
    api_key_env: VLLM_API_KEY      # optional; omit for an unauthenticated endpoint
```

`providers` maps a provider name to its connection settings. The three built-in names keep
working with no `providers` block at all, so every existing config is unaffected.

OpenRouter needs no special case — it is this shape with a different base URL:

```yaml
defaults:
  provider: openai_compatible
  model: qwen/qwen-2.5-72b-instruct

providers:
  openai_compatible:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
```

### Rolling your own

For an endpoint that does not speak the OpenAI protocol, a user registers an adapter the same way
they already register rule evals — a decorator in a file fieldtest loads from the project
directory:

```python
# evals/providers.py
from fieldtest import provider

@provider("my-inference-service")
def call(model: str, prompt: str, gen, retry) -> dict:
    """Return the judge's parsed JSON dict, or {"error": str}. Never raise."""
    ...
```

`load_providers()` mirrors `load_rules()`: same location convention, same memoization, same
`ConfigError` on import failure, and loaded by `score()` so every caller gets it. A registered
name satisfies `VALID_PROVIDERS` validation, so it can appear in `defaults.provider`, in a
per-eval override, and in a calibration panel with no further plumbing.

The function signature is deliberately the same shape as `ProviderAdapter.call()` rather than a
new one. A user who outgrows the decorator subclasses `ProviderAdapter` and registers the
instance; a user who never does is not asked to learn a class hierarchy to make one HTTP call.

```python
class ProviderSettings(BaseModel):
    base_url:    str
    api_key_env: Optional[str] = None


class OpenAICompatibleAdapter(ProviderAdapter):
    """
    Any endpoint speaking the OpenAI chat-completions protocol: vLLM, Ollama,
    Together, Fireworks, OpenRouter, xAI. Uses the openai SDK with base_url
    pointed elsewhere, so the request shape and the drop-and-rename path are
    shared with OpenAIAdapter rather than reimplemented.
    """
```

The judge fingerprint from spec 01 gains `base_url` for this provider type. Two runs against
`llama-3.3-70b-instruct` on different endpoints are not the same instrument, and a fingerprint
that ignored that would let `find_baseline()` compare across them.

## §4 Compatibility

Purely additive. `VALID_PROVIDERS` gains one name; a config with no `providers` block behaves
exactly as today. The existing three adapters are untouched.

`ProviderError` for an unknown provider gains a line pointing at `openai_compatible` for
endpoints fieldtest does not name directly, since that error is where a user discovers the
limitation.

## §5 Acceptance

Tests in `tests/test_providers.py` and `tests/test_config.py`:

- `test_openai_compatible_requires_base_url`
- `test_openai_compatible_works_without_an_api_key`
- `test_api_key_read_from_named_env_var`
- `test_api_key_never_read_from_config_literal`
- `test_unknown_provider_error_mentions_openai_compatible`
- `test_rejected_parameter_dropped_on_a_compatible_endpoint`
- `test_retry_policy_applies_to_a_compatible_endpoint`
- `test_fingerprint_includes_base_url`
- `test_fingerprint_differs_across_endpoints_for_the_same_model`
- `test_validate_reports_unset_provider_env_vars`
- `test_user_registered_provider_is_valid_in_config`
- `test_user_registered_provider_used_by_score`
- `test_user_registered_provider_may_appear_in_a_calibration_panel`
- `test_provider_registration_failure_is_a_config_error`
- `test_registered_provider_that_raises_produces_an_error_row_not_a_crash`

Behavioral acceptance: serve any small open-weight model with `vllm serve` or `ollama serve`,
point a config at it, and score a fixture set. Then run `fieldtest calibrate` with a panel mixing
that endpoint and a hosted model — a local judge and a frontier judge disagreeing on an eval is
the case this whole spec exists to make possible.

## §6 Out of scope

Shipping adapters for providers that do not speak the OpenAI protocol. Anthropic and Gemini earn
bespoke adapters because fieldtest already had them; a third bespoke shape in the box should have
to justify itself against `openai_compatible` and `@provider` first. The point of layer 1c is that
fieldtest does not have to predict which provider a user needs.

Also out of scope: validating that a registered provider behaves. A user's adapter that raises
instead of returning `{"error": ...}` produces one errored row, the same as any other judge
failure — `call_judge_llm()` already refuses to trust the type it gets back. fieldtest does not
sandbox user code here any more than it does for `@rule`.

And explicitly out of scope: a per-provider capability table for registered adapters. The three
built-in providers have already demonstrated why. Anthropic removed sampling parameters on its
5-series models, and OpenAI's reasoning models reject `temperature` and rename `max_tokens`. Both
were discovered by a call, neither by a table.

The table was also wrong in the other direction, which is the more instructive failure. It carried
an entry saying Gemini rejects `seed`. That entry came from misreading fieldtest's own hardcoded
`unsupported` list as an API response, and a live call later showed Gemini accepts `seed` (see
spec 12 §1). A registered provider therefore declares nothing about what it supports;
`call_dropping_unsupported()` finds out from the provider, and the report says what was dropped.

Also out of scope: judging the judges. Whether a 7B model is fit to score a `safe` eval is exactly
the question spec 08 answers with kappa and human labels, and it should be answered with evidence
from a calibration run rather than by fieldtest refusing to connect.
