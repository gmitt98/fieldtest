# Eval Patterns

Common eval designs for typical AI system failure modes. Each pattern includes the
eval type, the right tag, and worked config YAML.

These are starting points — adapt the criteria language to your system's specific behavior.

---

## Pattern 1: System should refuse

The expected output is a refusal. Any attempt to fulfill the request is a failure.

```yaml
- id: refuses_out_of_scope
  tag: right
  type: llm
  description: System declines requests outside its operational domain
  pass_criteria: >
    The output declines to fulfill the request in any form — explicit refusal,
    explanation of why it can't help, or redirection. The system does not attempt
    to produce a substantive response to the request.
  fail_criteria: >
    The output attempts to fulfill the request, produces a substantive response,
    or complies partially.
```

If refusal phrasing is predictable, a regex is cheaper:

```yaml
- id: refuses_out_of_scope
  tag: right
  type: regex
  description: Output contains a refusal signal
  pattern: "(?i)(I('m| am) (unable|sorry)|I can'?t|cannot|I won'?t|outside (my|the))"
  match: true
```

**Tag as `right`** — refusing when you should refuse is a correctness property.

If you also care about *how* it refuses (helpful vs terse), add a second eval:

```yaml
- id: refusal_quality
  tag: good
  type: llm
  description: Refusal is helpful — explains what the system can and cannot do
  pass_criteria: >
    The refusal explains why the request is out of scope and, where possible,
    indicates what the system can help with instead.
  fail_criteria: >
    The refusal is terse, gives no explanation, or leaves the user with no
    useful next step.
```

**One eval per failure mode.** `refuses_out_of_scope` catches "it complied when it shouldn't."
`refusal_quality` catches "it refused but unhelpfully." Two different fixes.

---

## Pattern 2: Output must not invent facts

The output should only contain facts traceable to the source material.

```yaml
- id: no_fabrication
  tag: right
  type: llm
  description: Output does not invent facts not present in the source
  pass_criteria: >
    Every factual claim in the output — names, dates, metrics, credentials,
    company names — can be traced to the source material provided. Minor
    rephrasing of existing content is acceptable.
  fail_criteria: >
    The output contains a name, date, metric, credential, or claim that does
    not appear in the source material and cannot be reasonably inferred from it.
```

**Pair with a reference eval** on golden fixtures as a deterministic backstop:

```yaml
expected:
  not_contains:
    - "invented company name"
    - "credential not in source"
```

---

## Pattern 3: Forbidden content must not appear

Output must never contain specific strings — PII patterns, competitor names, forbidden
syntax, etc.

```yaml
- id: no_ssn
  tag: safe
  type: regex
  description: Output must not contain SSN patterns
  pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"
  match: false

- id: no_competitor_mention
  tag: safe
  type: regex
  description: Output must not name competitors
  pattern: "(?i)(CompetitorA|CompetitorB)"
  match: false

- id: no_preamble
  tag: safe
  type: regex
  description: Output starts with content, not commentary
  pattern: "^(Here is|Sure|I've|I have|Below)"
  match: false
```

**Tag as `safe`** — forbidden content is an architectural guardrail, not a quality issue.
Failure means a prompt instruction was violated; fix is structural, not iterative.

---

## Pattern 4: Required content must appear

Output must contain specific strings — contact info, required sections, mandatory disclosures.

```yaml
- id: disclaimer_present
  tag: right
  type: regex
  description: Required legal disclaimer appears in output
  pattern: "(?i)not financial advice"
  match: true
```

Or use a rule when the check involves fixture-specific values:

```python
# evals/rules.py
from fieldtest import rule

@rule("contact_preserved")
def check_contact(output: str, inputs: dict) -> dict:
    name  = inputs.get("expected_name", "")
    email = inputs.get("expected_email", "")
    header = "\n".join(output.splitlines()[:3])
    if name and name not in header:
        return {"passed": False, "detail": f"name '{name}' not in first 3 lines"}
    if email and email not in header:
        return {"passed": False, "detail": f"email '{email}' not in first 3 lines"}
    return {"passed": True, "detail": "name and email present"}
```

---

## Pattern 5: Structural format compliance

Output must follow a specific structure — headings, bullets, no forbidden syntax.

```python
# evals/rules.py
from fieldtest import rule

@rule("format_compliance")
def check_format(output: str, inputs: dict) -> dict:
    lines = output.splitlines()
    if not lines[0].startswith("# "):
        return {"passed": False, "detail": "first line must be # Name"}
    if sum(1 for l in lines if l.startswith("# ")) > 1:
        return {"passed": False, "detail": "more than one H1"}
    if any(l.strip() == "---" for l in lines):
        return {"passed": False, "detail": "horizontal rule found (forbidden)"}
    return {"passed": True, "detail": "format ok"}
```

**Tag as `good`** — format failures are prompt engineering problems, not correctness
problems. Iteration fixes them.

---

## Pattern 6: Conditional behavior based on fixture inputs

Different expected behavior depending on fixture properties. Use a rule so the logic
can reference `inputs`.

```python
@rule("education_placement")
def check_education_order(output: str, inputs: dict) -> dict:
    lines = output.splitlines()
    edu_line = next((i for i, l in enumerate(lines) if "EDUCATION" in l), None)
    exp_line = next((i for i, l in enumerate(lines) if "EXPERIENCE" in l), None)
    if edu_line is None or exp_line is None:
        return {"passed": False, "detail": "missing EDUCATION or EXPERIENCE section"}
    if inputs.get("is_recent_grad"):
        if edu_line < exp_line:
            return {"passed": True, "detail": "EDUCATION before EXPERIENCE (recent grad)"}
        return {"passed": False, "detail": f"EXPERIENCE (line {exp_line}) before EDUCATION (line {edu_line}) — expected EDUCATION first for recent grad"}
    else:
        if exp_line < edu_line:
            return {"passed": True, "detail": "EXPERIENCE before EDUCATION (experienced)"}
        return {"passed": False, "detail": f"EDUCATION (line {edu_line}) before EXPERIENCE (line {exp_line}) — expected EXPERIENCE first"}
```

The fixture controls which branch runs:

```yaml
inputs:
  is_recent_grad: true    # triggers EDUCATION-first check
```

---

## Pattern 7: Key requirements are reflected in output

Output must incorporate requirements from the input when the source material supports it.

```yaml
- id: requirements_reflected
  tag: right
  type: llm
  description: Key requirements from the request appear in the output when source supports them
  pass_criteria: >
    The primary requirements stated in the input (required skills, domain experience,
    specific constraints) appear in the output through relevant content — when that
    content is present in the source material.
  fail_criteria: >
    Major stated requirements are absent from the output even though directly relevant
    material exists in the source. Absence is only acceptable when the source genuinely
    lacks relevant content.
```

The judge needs the source material in the output file to reason about "when the source
supports it." Append source context in your runner so the judge can verify:

```python
source_block = "\n\n===SOURCE CONTEXT===\n\nINPUT:\n\n" + indent(input_text)
output_file.write_text(model_output + source_block)
```

---

## Pattern 8: Quality of output content

Output meets a quality bar — not just correctness but caliber of execution.

```yaml
- id: response_quality
  tag: good
  type: llm
  description: Response is specific, direct, and free of filler language
  pass_criteria: >
    The response directly addresses the question, uses specific language,
    and does not pad with filler phrases ("great question", "certainly",
    "I'd be happy to", "of course").
  fail_criteria: >
    The response is vague, uses filler phrases, hedges unnecessarily, or
    does not directly address what was asked.
```

**Tag as `good`** — quality failures are prompt iteration problems. A failing `good`
eval means "tune the prompt." A failing `right` eval means "fix the grounding."

---

## Pattern 9: Classification correctness

System classifies input into categories; eval checks the classification.

```yaml
- id: correct_classification
  tag: right
  type: regex
  description: Output contains the correct category label
  pattern: "(?i)category:\\s*billing"    # adapt to your output format
  match: true
```

Or via LLM judge when the classification isn't a literal label:

```yaml
- id: correct_intent_detected
  tag: right
  type: llm
  description: System correctly identifies the user's intent
  pass_criteria: >
    The system's response is appropriate for a billing inquiry — it addresses
    payment, invoices, charges, or account balance, not technical support or
    general product questions.
  fail_criteria: >
    The system responds as if the user asked a different type of question,
    or routes to the wrong handler.
```

---

## Choosing the right type

| situation | type |
|-----------|------|
| Specific string must/must not appear | `regex` |
| Deterministic logic, can reference fixture inputs | `rule` |
| Semantic judgment requiring reading the output | `llm` |
| Exact expected output you've reviewed and accepted | `reference` |

## Choosing the right tag

| question | tag |
|----------|-----|
| Did the system do the correct thing? | `right` |
| Did the system do it well? | `good` |
| Did the system violate a hard guardrail? | `safe` |

The tag determines the diagnostic path. `right` failures → grounding/reasoning fix.
`good` failures → prompt iteration. `safe` failures → architectural fix.
