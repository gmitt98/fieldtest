# fieldtest demo — Email Support

This example shows fieldtest evaluating a customer support email assistant (Clearbook Support).
It covers three fixture types: a billing dispute with golden expected content, a product question,
and an upgrade request. One output (product-question run-3) contains a banned phrase
("100% guarantee") that triggers the `no-policy-invention` safety eval — watch it fail.

## Run it

```bash
fieldtest demo --example email
```

## What you're seeing

Six evals span RIGHT / GOOD / SAFE: address the ask, golden content check, greeting rule,
tone, policy invention guard, and unauthorized commitment guard. The `no-policy-invention`
regex eval will flag run-3 of product-question for the invented guarantee language.

## Experiments

**1. See the baseline**
```bash
fieldtest score
```
Review the report. Note that `no-policy-invention` fails for `product-question` run 3.

**2. Fix the failing output**
Open `evals/outputs/product-question/run-3.txt` and remove the "100% guarantee" phrase.
Re-run:
```bash
fieldtest score
```
Watch the failure clear in the next report.

**3. Add a new eval**
Open `evals/config.yaml` and add a new eval to the `evals:` list. For example, add a regex
eval that checks for a signature sign-off:

```yaml
- id: has-signoff
  tag: good
  labels: [format]
  type: regex
  description: Reply ends with a sign-off
  pattern: "(?i)(best regards|warm regards|sincerely|thank you)"
  match: true
```

Re-run `fieldtest score` — the new eval appears as a column in the report.

**4. Add a new fixture**
Create `evals/fixtures/variations/cancellation-request.yaml` with `id: cancellation-request`
and an `inputs.customer_email` field containing a cancellation scenario.
Add `cancellation-request` to the `full:` set in `config.yaml`.
Add `evals/outputs/cancellation-request/run-1.txt` (and run-2, run-3) with sample replies.
Re-run `fieldtest score` — the new fixture appears as a row in the matrix.
