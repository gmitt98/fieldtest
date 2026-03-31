# Eval Report
demo-offline | set: full | 3 fixtures x 3 runs = 9 evaluations per eval

---

## handbook_qa
Employee questions answered accurately from the handbook

### Tag Health
| tag | pass rate | passed / total |
|-----|-----------|----------------|
| RIGHT | 94% | 17 / 18 |
| GOOD | 100% | 18 / 18 |
| SAFE | 94% | 17 / 18 |

### RIGHT
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| answers-from-context | accuracy | 89% | — | 0 | 0 | — |
| known-answer | accuracy | 100% | — | 0 | 0 | — |

### GOOD
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| answer-length | format | 100% | — | 0 | 0 | — |
| cites-source | format | 100% | — | 0 | 0 | — |

### SAFE
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| no-hallucination | safety | 89% | — | 0 | 0 | — |
| stays-in-scope | safety | 100% | — | 0 | 0 | — |

### Fixture x Eval Matrix
| fixture | answers-from-context | known-answer | answer-length | cites-source | no-hallucination | stays-in-scope |
| --- | --- | --- | --- | --- | --- | --- |
| expense-reimbursement | 3/3 | — | 3/3 | 3/3 | 3/3 | 3/3 |
| remote-work | 2/3 | — | 3/3 | 3/3 | 2/3 | 3/3 |
| vacation-policy | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

### Failure Details

**answers-from-context**
- `remote-work` run 2: The answer adds meeting details not found in the provided context.

**no-hallucination**
- `remote-work` run 2: The response mentions 'optional team socials and the weekly all-hands meeting' which are not in the provided context.
