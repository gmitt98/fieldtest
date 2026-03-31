# fieldtest demo — Invoice Extraction

This example shows fieldtest evaluating a structured extraction system that converts
invoice text to JSON. Run-3 of invoice-complex includes an invented "discount" field
that was never in the source invoice — triggering the `no-invented-fields` regex safety eval.
This demo works without an API key for the rule and regex evals.

## Run it

```bash
fieldtest demo --example extraction
```

No API key required for the deterministic evals (valid-json, required-fields-present,
no-invented-fields). The LLM evals (extraction-quality, no-fabrication) require
`ANTHROPIC_API_KEY`.

## What you're seeing

Six evals cover JSON validity, required field presence, golden content check, extraction
accuracy, invented field guard, and fabrication guard. The `no-invented-fields` regex eval
flags run-3 of invoice-complex for adding a "discount" key not present in the source.

## Experiments

**1. See the baseline**
```bash
fieldtest score
```
Review the report. Note that `no-invented-fields` fails for `invoice-complex` run 3.

**2. Fix the failing output**
Open `evals/outputs/invoice-complex/run-3.txt` and remove the `"discount": "10%"` line.
Re-run:
```bash
fieldtest score
```
Watch the failure clear in the next report.

**3. Add a new eval**
Add a rule that checks the amount field is a valid number:

```python
# In evals/rules.py
@rule("amount-is-numeric")
def check_amount_numeric(output: str, inputs: dict) -> dict:
    import json, re
    try:
        data = json.loads(output.strip())
        amount = str(data.get("amount", ""))
        numeric = re.sub(r"[$,]", "", amount)
        float(numeric)
        return {"passed": True, "detail": f"amount '{amount}' is numeric"}
    except (json.JSONDecodeError, ValueError):
        return {"passed": False, "detail": "amount field is not a valid number"}
```

Add the eval to `config.yaml` under `evals:`, then re-run `fieldtest score`.

**4. Add a new fixture**
Create `evals/fixtures/variations/purchase-order.yaml` with `id: purchase-order`
and an `inputs.invoice_text` field. Add it to the `full:` set. Add output files
in `evals/outputs/purchase-order/`. Re-run `fieldtest score`.
