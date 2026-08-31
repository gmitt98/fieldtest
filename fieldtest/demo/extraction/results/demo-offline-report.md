# Eval Report
2026-08-27 10:39 | set: full | 3 fixtures × 3 runs = 9 scored output(s) per eval
judge: anthropic claude-haiku-4-5 | temperature: 0.0

---

## invoice_extraction
Extract structured fields from invoice text into validated JSON

### Tag Health
| tag | pass rate | passed / total |
|-----|-----------|----------------|
| RIGHT | 95% | 20 / 21 |
| GOOD | 33% | 3 / 9 |
| SAFE | 89% | 16 / 18 |

### RIGHT
| eval | labels | pass rate | n | mean | floor hits | errors | vs prior |
|------|--------|----------|---|------|-----------|--------|---------|
| valid-json | structure | 100% [70–100%] | 9 | — | 0 | 0 | — |
| required-fields-present | structure|completeness | 100% [70–100%] | 9 | — | 0 | 0 | — |
| known-extraction | accuracy | 67% [21–94%] | 3 | — | 0 | 0 | — |

### GOOD
| eval | labels | pass rate | n | mean | floor hits | errors | vs prior |
|------|--------|----------|---|------|-----------|--------|---------|
| extraction-quality | accuracy | 33% [12–65%] | 9 | — | 0 | 0 | — |

### SAFE
| eval | labels | pass rate | n | mean | floor hits | errors | vs prior |
|------|--------|----------|---|------|-----------|--------|---------|
| no-invented-fields | integrity | 89% [56–98%] | 9 | — | 0 | 0 | — |
| no-fabrication | integrity | 89% [56–98%] | 9 | — | 0 | 0 | — |

### Fixture × Eval Matrix
| fixture | valid-json | required-fields-present | known-extraction | extraction-quality | no-invented-fields | no-fabrication |
| --- | --- | --- | --- | --- | --- | --- |
| invoice-complex | 3/3 | 3/3 | — | 2/3 | 2/3 | 2/3 |
| invoice-simple | 3/3 | 3/3 | 2/3 | 1/3 | 3/3 | 3/3 |
| receipt | 3/3 | 3/3 | — | 0/3 | 3/3 | 3/3 |

### Failure Details

**extraction-quality**
- `invoice-complex` run 3: The discount field shows '10%' but no discount is mentioned in the source text, and the amount is missing the currency symbol ($) that was present in the source.
- `invoice-simple` run 1: The amount field is missing the currency symbol ($) that was present in the source text, violating the requirement that amounts include currency where present in source.
- `invoice-simple` run 3: The amount field is missing the currency symbol ($) that was present in the source text, violating the requirement that amounts include currency where present in source.
- `receipt` run 1: The amount field is missing the currency symbol ($) that was present in the source text, violating the requirement that amounts include currency where present in source.
- `receipt` run 2: The amount field is missing the currency symbol ($) that was present in the source text, violating the requirement that amounts include currency where present in source.
- `receipt` run 3: The amount field is missing the currency symbol ($) that was present in the source text, violating the requirement that amounts include currency where present in source.

**known-extraction**
- `invoice-simple` run 2: missing: 3420

**no-fabrication**
- `invoice-complex` run 3: The discount value of '10%' does not appear anywhere in the source invoice text and appears to be invented rather than extracted.

**no-invented-fields**
- `invoice-complex` run 3: pattern '(?i)("discount"|"tax_rate"|"notes"|"payment_terms"|"late_fee")' found
