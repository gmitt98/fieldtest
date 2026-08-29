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
| 01 | [Judge provenance in run metadata](01-judge-provenance.md) | 1 | none | shipped |
| 02 | [Judge generation config](02-judge-generation-config.md) | 1 | none | shipped |
| 03 | [Judge prompt hardening](03-judge-prompt-hardening.md) | 1 | none | shipped |
| 04 | [Failure rate intervals](04-failure-rate-intervals.md) | 1 | none | shipped |
| 05 | [Provider retry parity](05-provider-retry-parity.md) | 1 | 02 | shipped |
| 06 | [Judge variance decomposition](06-judge-variance-decomposition.md) | 2 | 01, 02 | shipped |
| 07 | [Human labels in fixtures](07-human-labels.md) | 2 | none | shipped |
| 08 | [`fieldtest calibrate`](08-calibrate-command.md) | 2 | 01, 02, 06, 07 | shipped |
| 09 | [Discovery position](09-discovery-position.md) | 3 | none | shipped |
| 10 | [Inspect interop](10-inspect-interop.md) | 3 | 01 | sketch |
| 11 | [Provider surface beyond the big three](11-provider-surface.md) | 2 | 02, 05 | shipped |
| 12 | [Verification tiers](12-verification-tiers.md) | 2 | 11 | shipped |
| 13 | [Judge input visibility](13-judge-input-visibility.md) | 1 | 03 | shipped |
| 14 | [Sample datasets](14-sample-datasets.md) | 3 | 13 | draft |

## Version boundary

Specs 01, 04, 06, and 07 change the shape of `-data.json` and the fixture schema. Ship them
together as `schema_version: 2` rather than dribbling additive fields into version 1.

**Shipped.** 01 and 04 landed together as the `schema_version: 2` boundary; 06 and 07 followed as
additive fields on version 2 rather than waiting, since the boundary had already moved. Readers
of `-data.json` in CI already depend on the documented shape, and the README publishes that
shape as a gating contract.

`parse_and_validate()` accepts `schema_version: 1` configs unchanged for one minor release,
defaulting all v2 fields to values that reproduce v1 behavior exactly. See each spec's
compatibility section.

## Added after the fact

Specs 11 and 12 were written after live verification, not before it. Two providers turned out to have
removed generation parameters fieldtest depends on, which made the adapter layer's real shape
visible: parameter support is per model and changes on the provider's schedule, so an adapter
that discovers support beats one that declares it. Once that was true, supporting endpoints
fieldtest does not ship an adapter for became cheap rather than speculative.

Spec 12 follows from the same run. Four defects reached a release through a suite of 308 passing
tests, and the reason is the one fieldtest exists to point at: a mock agrees with the code that
built it, the way a judge agrees with itself. The tool asks its users to characterize their
instrument; its own suite had not.

## What is deliberately not here

No solver layer, no sandbox, no tool calling, no multimodal fixtures, no transcript scanners.
The generator-writes-files contract in `outputs/{fixture_id}/run-N.txt` is why fieldtest can
skip that surface, and it is the reason specs 06 and 08 are cheap here and expensive elsewhere:
rescoring a historical output set costs a directory read.
