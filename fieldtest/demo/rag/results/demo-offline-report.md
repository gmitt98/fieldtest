# Eval Report
2026-03-31 23:16 | set: full | 4 fixtures × 3 runs = 12 evaluations per eval

---

## handbook_qa
Employee questions answered accurately from the handbook

### Tag Health
| tag | pass rate | passed / total |
|-----|-----------|----------------|
| RIGHT | 81% | 17 / 21 |
| GOOD | 96% | 23 / 24 |
| SAFE | 88% | 21 / 24 |

### RIGHT
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| answers-from-context | accuracy | 83% | — | 0 | 0 | — |
| known-answer | accuracy | 78% | — | 0 | 0 | — |

### GOOD
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| answer-length | format | 100% | — | 0 | 0 | — |
| cites-source | format | 92% | — | 0 | 0 | — |

### SAFE
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| no-hallucination | grounding | 83% | — | 0 | 0 | — |
| stays-in-scope | grounding | 92% | — | 0 | 0 | — |

### Fixture × Eval Matrix
| fixture | answers-from-context | known-answer | answer-length | cites-source | no-hallucination | stays-in-scope |
| --- | --- | --- | --- | --- | --- | --- |
| expense-reimbursement | 3/3 | 2/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| out-of-scope | 2/3 | — | 3/3 | 2/3 | 2/3 | 2/3 |
| remote-work | 2/3 | 2/3 | 3/3 | 3/3 | 2/3 | 3/3 |
| vacation-policy | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

### Failure Details

**answers-from-context**
- `out-of-scope` run 3: answer makes claims not supported by the provided PTO excerpt
- `remote-work` run 2: The answer adds meeting details not found in the provided context.

**cites-source**
- `out-of-scope` run 1: pattern not found

**known-answer**
- `expense-reimbursement` run 2: missing: 'manager approval'
- `remote-work` run 2: found forbidden: 'required to attend optional'

**no-hallucination**
- `out-of-scope` run 3: fabricated core hours (9–5, 10–3) not present in the provided PTO excerpt
- `remote-work` run 2: The response mentions 'optional team socials and the weekly all-hands meeting' which are not in the provided context.

**stays-in-scope**
- `out-of-scope` run 3: answered a question about remote work despite only having PTO context
