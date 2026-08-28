# Spec 09 — Discovery position

**Tier** 3 · **Depends on** none · **Touches** `docs/philosophy.md`, `README.md` · **Status** shipped

## §1 Problem

Config-first means a failure mode has to be named before it can be measured. That is the thesis,
and it is also a structural limit: fieldtest cannot surface a failure nobody anticipated.

Inspect ships Scanners for exactly this. They search transcripts after the fact for issues that
were not encoded in any scorer at authoring time. A reader who knows both tools will notice the
asymmetry, and leaving it unaddressed reads as an oversight rather than a position.

There is an answer, and it is already built. `-data.csv` is a flat row per fixture × eval × run
with `detail` carrying the judge's reasoning on every row, and it exists precisely so the data
can leave the tool. Discovery happens in the analysis layer. That is a deliberate architectural
choice and it should be written down as one.

## §2 Scope

Add a section to `docs/philosophy.md`, roughly the length of the existing argument, making these
points:

1. Naming failure modes in advance is the practice. The cost of that practice is that
   unanticipated failures do not appear in the report.
2. That cost is paid deliberately. A tool that surfaces anomalies you did not define lets you
   skip the definition step, which is the behavior fieldtest exists to prevent.
3. Discovery is not thereby abandoned. It moves to `-data.csv`, where `detail` text across every
   fixture, eval, and run is available to whatever analysis the team already runs. A `safe` eval
   passing on every fixture while the reasoning text keeps mentioning an unmodeled edge case is
   discoverable, and it is discoverable in a notebook rather than in the tool.
4. The loop closes by writing the new eval. Discovery in the analysis layer produces a candidate
   failure mode; the practice says you then name it, tag it, and add it to the config. The
   discovery is not the measurement.

Add one line to the README positioning paragraph so a comparison shopper hits the position rather
than the gap.

## §3 Acceptance

No tests. Acceptance is that a reader who knows Inspect finishes the philosophy doc understanding
this as a choice with a stated cost.

## §4 Note

If a `fieldtest scan` command is ever built, this document is the argument it has to survive.
Anything that surfaces unnamed failures directly in the report contradicts the thesis, and the
right shape would be an analysis helper over `-data.csv` that proposes candidate evals for the
config rather than scoring anything.
