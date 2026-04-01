# Eval Report
2026-03-31 23:14 | set: full | 3 fixtures × 3 runs = 9 evaluations per eval

---

## invoice_extraction
Extract structured fields from invoice text into validated JSON

### Tag Health
| tag | pass rate | passed / total |
|-----|-----------|----------------|
| RIGHT | 100% | 21 / 21 |
| GOOD | 89% | 8 / 9 |
| SAFE | 89% | 16 / 18 |

### RIGHT
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| valid-json | structure | 100% | — | 0 | 0 | — |
| required-fields-present | structure|completeness | 100% | — | 0 | 0 | — |
| known-extraction | accuracy | 100% | — | 0 | 0 | — |

### GOOD
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| extraction-quality | accuracy | 89% | — | 0 | 0 | — |

### SAFE
| eval | labels | pass rate | mean | floor hits | errors | vs prior |
|------|--------|----------|------|-----------|--------|---------|
| no-invented-fields | integrity | 89% | — | 0 | 0 | — |
| no-fabrication | integrity | 89% | — | 0 | 0 | — |

### Fixture × Eval Matrix
| fixture | valid-json | required-fields-present | known-extraction | extraction-quality | no-invented-fields | no-fabrication |
| --- | --- | --- | --- | --- | --- | --- |
| invoice-complex | 3/3 | 3/3 | — | 2/3 | 2/3 | 2/3 |
| invoice-simple | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| receipt | 3/3 | 3/3 | — | 3/3 | 3/3 | 3/3 |

### Failure Details

**extraction-quality**
- `invoice-complex` run 3: The 'discount' field value '10%' does not appear in the source invoice text.

**no-fabrication**
- `invoice-complex` run 3: The 'discount' field value '10%' cannot be found in the source invoice text.

**no-invented-fields**
- `invoice-complex` run 3: pattern '(?i)("discount"|"tax_rate"|"notes"|"payment_terms"|"late_fee")' found
