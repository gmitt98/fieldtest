# Recipe: RAG Faithfulness

**Goal:** Eval a retrieval-augmented system for hallucination, grounding, and answer quality.

## The problem

RAG systems fail in specific ways: they hallucinate details not in the retrieved context, they answer questions the context doesn't support, and they sometimes ignore relevant context entirely. These are different failure modes and need different evals.

## Config snippet

```yaml
evals:
  # RIGHT — is the answer correct given the context?
  - id: answers-from-context
    tag: right
    labels: [accuracy]
    type: llm
    description: Every claim in the answer is supported by the provided context
    pass_criteria: All specific claims can be traced to the retrieved excerpt
    fail_criteria: Answer makes claims not present in the provided context

  - id: known-answer
    tag: right
    labels: [accuracy]
    type: reference
    description: Golden answer check for fixtures with known correct answers

  # GOOD — is the answer well-formed?
  - id: cites-source
    tag: good
    labels: [completeness]
    type: regex
    pattern: "(?i)(section|handbook|policy|per the|according to)"
    match: true
    description: Answer references the source document

  # SAFE — does it stay in bounds?
  - id: no-hallucination
    tag: safe
    labels: [grounding]
    type: llm
    description: No fabricated details beyond the source material
    pass_criteria: Every specific detail can be found in the context
    fail_criteria: Any detail appears invented or added beyond the source

  - id: stays-in-scope
    tag: safe
    labels: [grounding]
    type: llm
    description: Declines questions not answerable from the provided context
    pass_criteria: Redirects or acknowledges the question is outside available context
    fail_criteria: Fabricates an answer for out-of-scope questions
```

## Fixture design

Include the retrieved context in your fixture inputs so judges can verify grounding:

```yaml
# evals/fixtures/golden/vacation-policy.yaml
id: vacation-policy
inputs:
  question: "How many vacation days do new employees get?"
  context: |
    Section 4.2 — PTO Policy
    New employees accrue 15 days of paid time off in their first year...
expected:
  contains:
    - "15 days"
```

**Critical:** Add at least one out-of-scope fixture — a question the context can't answer. This is where `stays-in-scope` and `no-hallucination` earn their keep.

## What the report shows

- **RIGHT** pass rate tells you if the system is answering correctly
- **SAFE** pass rate tells you if it's making things up
- The `grounding` label lets you filter the HTML report to see all grounding-related evals at once
- Out-of-scope fixtures that pass all evals = your guardrails work; failures there = architectural fix needed
