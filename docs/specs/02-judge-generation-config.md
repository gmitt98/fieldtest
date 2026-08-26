# Spec 02 — Judge generation config

**Tier** 1 · **Depends on** none · **Touches** `providers/base.py`, `providers/anthropic.py`, `providers/openai.py`, `providers/gemini.py`, `judges/llm.py`, `config.py`

## §1 Problem

`ProviderAdapter.call(self, model: str, prompt: str) -> dict` has no parameter for generation
settings, so no adapter can set them.

`AnthropicAdapter` sends `model`, `max_tokens=2048`, `messages`. `OpenAIAdapter` sends the same
three. `GeminiAdapter` sends `model` and `contents` and does not bound output length at all.
None sets temperature. Every judge in fieldtest therefore runs at whatever the provider's default
sampling temperature happens to be, which for most providers is 1.0.

The consequence is that a scored eval's `stddev` and a binary eval's `failure_rate` both move
between runs for reasons that have nothing to do with the system under test, and the tool reports
no way to tell. Spec 06 cannot decompose variance until the judge can be held still.

## §2 Requirements

1. `ProviderAdapter.call()` accepts a generation config object alongside model and prompt.
2. `config.Defaults` gains `judge_temperature: float = 0.0` and `judge_seed: Optional[int] = None`.
3. Temperature defaults to 0.0, not to the provider default. A measurement tool should ship its
   instrument locked, and a user who wants sampling noise can ask for it explicitly.
4. Every adapter bounds output length. Gemini currently does not.
5. Where a provider does not support a requested parameter, the adapter ignores it and records
   the fact once per run rather than failing. Seed is the live case: Anthropic has no seed
   parameter.
6. Per-provider parameter support is documented in the README provider table, not discovered at
   runtime by the user.
7. `judge_temperature` and `judge_seed` join the spec 01 fingerprint payload.

## §3 Contract

```python
class JudgeGenerationConfig(BaseModel):
    temperature: float = 0.0
    seed:        Optional[int] = None
    max_tokens:  int = 2048


class ProviderAdapter(ABC):
    @abstractmethod
    def call(self, model: str, prompt: str, gen: JudgeGenerationConfig) -> dict:
        """
        Call the LLM and return parsed JSON dict.
        Returns {"error": str} on failure — never raises.
        Ignores unsupported parameters in `gen`; records them in `unsupported`.
        """
```

Successful returns may carry an optional `unsupported: list[str]` key naming parameters the
provider dropped. `call_judge_llm()` collects these and the runner surfaces the distinct set once
in the report header, not per row.

Provider support matrix as of this spec:

| provider | temperature | seed | max_tokens |
|---|---|---|---|
| anthropic | yes | no | yes |
| openai | yes | yes | yes |
| gemini | yes | no | yes |

## §4 Compatibility

`schema_version: 1` configs get `judge_temperature: 0.0`. This changes results for existing
users, because their judges were previously sampling. That is the point of the change, and it
must be called out in the changelog rather than shipped quietly: rates will move on upgrade, and
the movement is the removal of noise, not a regression.

Anyone who wants the old behavior sets `judge_temperature: 1.0` explicitly.

## §5 Acceptance

Tests in `tests/test_providers.py` and `tests/test_config.py`:

- `test_adapter_call_accepts_generation_config`
- `test_judge_temperature_defaults_to_zero`
- `test_anthropic_adapter_reports_seed_unsupported`
- `test_gemini_adapter_sets_max_tokens`
- `test_unsupported_params_surface_once_in_report`
- `test_v1_config_gets_zero_temperature_default`

Behavioral acceptance: run `fieldtest score` twice over an unchanged `outputs/` directory with
an LLM eval and confirm identical `passed` values on every row. Where a provider still returns
disagreement at temperature 0, that residual is a real property of the provider and belongs in
spec 06's judge variance number rather than being hidden.

## §6 Out of scope

Measuring the residual disagreement is spec 06. This spec only makes it possible to ask the judge
the same question twice under the same conditions.
