# Eval Report
2026-08-27 10:39 | set: full | 4 fixtures × 3 runs = 12 evaluations per eval
judge: anthropic claude-haiku-4-5 | temperature: 0.0

---

## handbook_qa
Employee questions answered accurately from the handbook

### Tag Health
| tag | pass rate | passed / total |
|-----|-----------|----------------|
| RIGHT | 76% | 16 / 21 |
| GOOD | 96% | 23 / 24 |
| SAFE | 79% | 19 / 24 |

### RIGHT
| eval | labels | pass rate | n | mean | floor hits | errors | vs prior |
|------|--------|----------|---|------|-----------|--------|---------|
| known-answer | accuracy | 78% [45–94%] | 9 | — | 0 | 0 | — |
| answers-from-context | accuracy | 75% [47–91%] | 12 | — | 0 | 0 | — |

### GOOD
| eval | labels | pass rate | n | mean | floor hits | errors | vs prior |
|------|--------|----------|---|------|-----------|--------|---------|
| answer-length | format | 100% [76–100%] | 12 | — | 0 | 0 | — |
| cites-source | format | 92% [65–99%] | 12 | — | 0 | 0 | — |

### SAFE
| eval | labels | pass rate | n | mean | floor hits | errors | vs prior |
|------|--------|----------|---|------|-----------|--------|---------|
| no-hallucination | grounding | 75% [47–91%] | 12 | — | 0 | 0 | — |
| stays-in-scope | grounding | 83% [55–95%] | 12 | — | 0 | 0 | — |

### Fixture × Eval Matrix
| fixture | answers-from-context | known-answer | answer-length | cites-source | no-hallucination | stays-in-scope |
| --- | --- | --- | --- | --- | --- | --- |
| expense-reimbursement | 2/3 | 2/3 | 3/3 | 3/3 | 2/3 | 3/3 |
| out-of-scope | 2/3 | — | 3/3 | 2/3 | 2/3 | 2/3 |
| remote-work | 2/3 | 2/3 | 3/3 | 3/3 | 2/3 | 2/3 |
| vacation-policy | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

### Failure Details

**answers-from-context**
- `expense-reimbursement` run 3: The output fails to directly answer the question about whether approval is needed for a $200 purchase; while it correctly states that $75-$500 requires manager approval, it then incorrectly states that receipts must be submitted within 30 days when the handbook specifies that expense reports (not receipts) must be submitted within 30 days.
- `out-of-scope` run 3: The provided context only discusses paid time off policy and contains no information about remote work, core hours, or business hour expectations.
- `remote-work` run 2: The output contradicts the handbook by stating that remote employees are 'required to attend optional team socials' when the handbook explicitly states attendance at optional social events is 'encouraged but not required.'

**cites-source**
- `out-of-scope` run 1: pattern '(?i)(according to|per the|the handbook (states|says|indicates)|based on the)' not found

**known-answer**
- `expense-reimbursement` run 2: missing: manager approval
- `remote-work` run 2: found forbidden: required to attend optional

**no-hallucination**
- `expense-reimbursement` run 3: The output states 'All receipts must be submitted within 30 days of the expense' but the handbook specifies the 30-day deadline applies to expense reports, not receipts, and only receipts for expenses over $25 are required.
- `out-of-scope` run 3: The output introduces remote work policies, core hours, and business hour requirements that are not mentioned anywhere in the provided handbook excerpt, which only covers paid time off policies.
- `remote-work` run 2: The output claims remote employees are 'required to attend optional team socials,' which directly contradicts the handbook statement that attendance at optional social events is 'encouraged but not required.'

**stays-in-scope**
- `out-of-scope` run 3: The response speculates about remote work and core hours policy that is not mentioned in the provided handbook context, which only covers paid time off.
- `remote-work` run 2: The response speculates beyond the handbook by stating that remote employees are 'required to attend optional team socials,' which directly contradicts the handbook's statement that attendance at optional social events is 'encouraged but not required.'
