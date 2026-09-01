# Spec 15 — One concept, one home

**Tier** 0 · **Touches** everything · **Status** partial

## §1 Problem

Across five audit rounds of 0.3.0, roughly 110 defects were fixed. A large share
of them — including every blocker introduced *by a fix* — has one shape:

> A concept has several homes. One of them is changed. The others are not.

| Defect | The concept | Homes |
|---|---|---|
| `view` crashed without `--config` | resolving the default config path | 7 commands, 6 shared a helper |
| `init --force` overwrote a config silently | warning on overwrite | 2 branches of one conditional |
| `clean` locked users out of their own project | "is this a fieldtest project" | 1 home, wrong predicate |
| exit gate disarmed by a passing regex | "a judge call" | `score` and `calibrate`, separately |
| HTML deltas painted regressions green | direction of a delta | 2 renderers |
| `history` showed 12% where the report showed 95% | a tag's rate | 2 commands |
| fixtures in `golden/` unreachable | locating a fixture file | 4 call sites |

None of these was subtle in isolation. Each was invisible because the fix and
the test that covered it were written from the same understanding, and that
understanding stopped at the home in front of the author.

## §2 What does not work

**Care.** Every one of these was written carefully, with a comment explaining
the intent. `view`'s comment said "default=None so this goes through
`_default_config_path()`" — and the call was never added. Stating intent in prose
beside the code is what produced the bug, not what prevented it.

**Expectation tests.** A test written by the author of a fix asserts what the
author believed. `view` had four tests; every one passed `--config`.

## §3 What works

**Inventory tests.** Assertions over the codebase rather than over a value:

- no code path joins a fixture id flat (`test_no_code_path_looks_up_a_fixture_flat`)
- every command resolves its config default through the shared helper
- every command the docs tell you to type exists, with those flags and values
- the HTML and markdown renderers agree on the sign of a delta
- `history` and the report agree on a tag's rate
- `judge_calls` counts only evals that call a judge
- no test module imports a 3.11-only stdlib module

These do not depend on the author imagining the failing case, which is exactly
the faculty that fails. Each one above was added *after* the defect it describes
shipped; each has since been checked against a mutation that reintroduces it.

**Collapsing the homes.** Where a concept was reduced to one function —
`find_fixture_path`, `result_files_newest_first`, `find_result_by_run_id` — the
class of defect stopped recurring. Where it was not, it recurred.

## §4 Standing rule

When fixing a defect, before writing the fix:

1. Name the concept, not the symptom.
2. Grep for every home. If there is more than one, that is the finding.
3. Check the sibling branches of any conditional you touch.
4. Look for prior art: this concept may already be solved elsewhere in the
   codebase, correctly, with a test explaining why. `calibrate` had already
   fixed `judge_calls`; `score` was left with it for a month.

Then fix every home, or collapse them, and write the inventory test that fails
when they diverge again.

## §5 Known remaining duplication

Measured, not estimated. These are the concepts still computed in more than one
place, highest first:

| Concept | Sites | Files |
|---|---|---|
| llm-ness of an eval | 13 | 9 |
| run id from a filename | 12 | 6 |
| results directory path | 11 | 4 |
| config default resolution | 10 | 4 |
| delta direction | 7 | 2 |
| a tag's pass rate | 6 | 3 |
| scored-ness of an eval | 4 | 4 |

Each of the last three has an agreement tripwire; the first four do not yet.
Collapsing them is a refactor, and doing it immediately before a release —
with the defect rate this document exists to describe — would be the same
mistake in a new form. It is the first work after 0.3.0 ships.
