# Spec 11 — Provider surface beyond the big three

**Tier** 2 · **Depends on** 02, 05 · **Touches** `providers/`, `config.py`, README · **Status** draft

## §1 Problem

`get_provider_adapter()` knows three names: `anthropic`, `openai`, `gemini`. Anything else raises
`ProviderError`. A user who judges with Grok, with a model on Together or Fireworks, or with an
open-weight model they serve themselves through vLLM or Ollama, cannot use fieldtest at all.

That is a larger exclusion than it looks. The generator-writes-files contract means fieldtest
never runs the system under test, so the *only* thing it needs a provider for is the judge. Being
unable to name a judge is being unable to use the tool.

It also cuts against the v1 argument. The README's position is that the taxonomy — right, good,
safe — is the thing fieldtest contributes, and that the runtime belongs to whoever already has
one. A tool that classifies failures should not also dictate which lab you buy judgment from.

The immediate trigger is narrower and already proven. Live verification found that Anthropic
removed sampling parameters on its newest models and that OpenAI's reasoning models reject both
`temperature` and `max_tokens`. Both were absorbed by `call_dropping_unsupported()` in
`providers/base.py`, which drops or renames a parameter the provider refuses. That machinery is
provider-agnostic by construction, and it is what makes a fourth adapter cheap rather than a
fourth maintenance burden.

## §2 Requirements

1. A user can point fieldtest at any endpoint that speaks the OpenAI chat-completions protocol,
   without fieldtest shipping a named adapter for it.
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

Behavioral acceptance: serve any small open-weight model with `vllm serve` or `ollama serve`,
point a config at it, and score a fixture set. Then run `fieldtest calibrate` with a panel mixing
that endpoint and a hosted model — a local judge and a frontier judge disagreeing on an eval is
the case this whole spec exists to make possible.

## §6 Out of scope

Adapters for providers that do not speak the OpenAI protocol. Anthropic and Gemini earn bespoke
adapters because fieldtest already had them; a third bespoke shape should have to justify itself
against `openai_compatible` first.

Also out of scope: judging the judges. Whether a 7B model is fit to score a `safe` eval is exactly
the question spec 08 answers with kappa and human labels, and it should be answered with evidence
from a calibration run rather than by fieldtest refusing to connect.
