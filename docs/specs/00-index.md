# fieldtest v2 specs

The v1 spec (§7 CLI, §8 judges, §9 aggregation, §16 config, §17 error contract) defined the
measurement layer. These specs define the layer above it: characterizing the instrument that
produces the measurements.

The v1 argument is that teams measure without defining what good means. The v2 argument follows
directly from it. A `failure_rate` is a claim about a system produced by a judge whose accuracy,
repeatability, and identity are currently unrecorded. fieldtest asks its users to define good
before measuring, and does not yet hold itself to the same standard.

## Sequencing

Tier 1 corrects defects in the existing instrument. Nothing in tier 2 is trustworthy until
tier 1 lands, because calibration numbers computed on a nondeterministic, unversioned judge
measure nothing.

| # | Spec | Tier | Depends on | Status |
|---|------|------|-----------|--------|
| 01 | [Judge provenance in run metadata](01-judge-provenance.md) | 1 | none | draft |
| 02 | [Judge generation config](02-judge-generation-config.md) | 1 | none | draft |
| 03 | [Judge prompt hardening](03-judge-prompt-hardening.md) | 1 | none | draft |
| 04 | [Failure rate intervals](04-failure-rate-intervals.md) | 1 | none | draft |
| 05 | [Provider retry parity](05-provider-retry-parity.md) | 1 | 02 | draft |
| 06 | [Judge variance decomposition](06-judge-variance-decomposition.md) | 2 | 01, 02 | draft |
| 07 | [Human labels in fixtures](07-human-labels.md) | 2 | none | draft |
| 08 | [`fieldtest calibrate`](08-calibrate-command.md) | 2 | 01, 02, 06, 07 | draft |
| 09 | [Discovery position](09-discovery-position.md) | 3 | none | sketch |
| 10 | [Inspect interop](10-inspect-interop.md) | 3 | 01 | sketch |

## Version boundary

Specs 01, 04, 06, and 07 change the shape of `-data.json` and the fixture schema. Ship them
together as `schema_version: 2` rather than dribbling additive fields into version 1. Readers
of `-data.json` in CI already depend on the documented shape, and the README publishes that
shape as a gating contract.

`parse_and_validate()` accepts `schema_version: 1` configs unchanged for one minor release,
defaulting all v2 fields to values that reproduce v1 behavior exactly. See each spec's
compatibility section.

## What is deliberately not here

No solver layer, no sandbox, no tool calling, no multimodal fixtures, no transcript scanners.
The generator-writes-files contract in `outputs/{fixture_id}/run-N.txt` is why fieldtest can
skip that surface, and it is the reason specs 06 and 08 are cheap here and expensive elsewhere:
rescoring a historical output set costs a directory read.
