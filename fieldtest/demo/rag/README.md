# fieldtest demo — RAG Handbook Q&A

This example shows fieldtest evaluating a RAG system that answers employee questions
from the Meridian Corp employee handbook. One output (remote-work run-2) introduces
information not in the handbook context — "optional team socials and the weekly all-hands
meeting" — which triggers the `no-hallucination` safety eval.

## Run it

```bash
fieldtest demo --example rag
```

## What you're seeing

Six evals cover grounding accuracy, golden answer check, word count, source citation,
hallucination guard, and scope adherence. The `no-hallucination` LLM eval flags run-2
of remote-work for adding fabricated policy details beyond what the context supports.

## Experiments

**1. See the baseline**
```bash
fieldtest score
```
Review the report. Note that `no-hallucination` fails for `remote-work` run 2.

**2. Fix the failing output**
Open `evals/outputs/remote-work/run-2.txt` and remove the invented "optional team socials"
sentence. Re-run:
```bash
fieldtest score
```
Watch the hallucination failure clear in the next report.

**3. Add a new eval**
Open `evals/config.yaml` and add a regex eval that checks the response cites a section number:

```yaml
- id: cites-section-number
  tag: good
  labels: [format]
  type: regex
  description: Response cites a handbook section number
  pattern: "(?i)section \\d+\\.\\d+"
  match: true
```

Re-run `fieldtest score` — the new eval appears as a column in the report.

**4. Add a new fixture**
Create `evals/fixtures/variations/parental-leave.yaml` with `id: parental-leave`,
an `inputs.context` field with a handbook excerpt about parental leave policy,
and an `inputs.question` field. Add `parental-leave` to the `full:` set in `config.yaml`.
Add output files in `evals/outputs/parental-leave/`. Re-run `fieldtest score`.
