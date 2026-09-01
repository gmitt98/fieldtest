# Spec 16 — Coverage census of 0.3.0

**Tier** 0 · **Status** complete · **Method** stdlib arc coverage, five agents with
disjoint file ownership, read-only

## §1 What was measured

Arc coverage over `fieldtest/` while the suite runs: branches never taken, `except`
handlers never raised, functions never entered. 255 regions had no coverage. Each
was classified by an agent that owned that file exclusively and had to *run* a
reproduction before calling anything reachable.

| Classification | Count | Meaning |
|---|---|---|
| MISTRACED | 133 | does run under an existing test; the tracer missed it |
| REACHABLE | 91 | a user can hit it; reproduction executed |
| DEFENSIVE | 18 | reachable only if another invariant is already broken |
| DEAD | 13 | cannot be reached by any input |

## §2 The tool was wrong about half of it

133 of 255 were MISTRACED — they execute under tests the tracer could not follow
(subprocess boundaries, C-level calls). A stdlib `sys.settrace` census has a
false-positive rate above 50% on this codebase. That is a fact about the
instrument, not the suite, and anyone repeating this should expect it: the
classification pass is not optional polish, it is what makes the census mean
anything.

## §3 Six defects, one shape

Every REACHABLE region that misbehaved was a bare `except Exception` that
discarded the real error and let the code state something false in its place —
`validate` blaming a missing decorator for an import failure, `diff` calling an
unreadable baseline old, `calibrate --dry-run` costing a bad set at zero. All six
are fixed, and `test_no_command_discards_an_error_and_carries_on_silently` lists
every remaining silent handler against an allowlist.

## §4 Deliberately kept

These are unreached and stay that way. Deleting a cheap guard on a public
function buys nothing and removes a real protection if a second caller appears.
The record exists so the next census does not re-litigate them.

| Region | Kind | Why it stays |
|---|---|---|
| `results/aggregator.py:549` | then-branch never taken | `if calls is None: calls = scored + errors` — a schema-v1 compatibility path for summaries writt |
| `results/html.py:640` | else-branch never taken | The else body is line 645, `label = f"{pass_pct}% n={n}"` — the matrix-footer path for an eval w |
| `results/report.py:91` | then-branch never taken | `if (eid, tag) in seen: continue` in _format_fixture_matrix |
| `results/report.py:400` | else-branch never taken | The else body is line 404, `pr_str = f"{round((1 - fr) * 100)}%"` — the pass-rate cell for an ev |
| `results/provenance.py:110` | then-branch never taken | `if not current or not baseline: return None` in describe_judge_change |
| `providers/base.py:279` | function never called | Genuinely uncalled — confirmed by the call-event trace (the only region in this set where 'funct |
| `fixtures.py:21` | then-branch never taken | typing |
| `fixtures.py:46` | then-branch never taken | resolve_file_inputs has exactly one in-repo caller, fixtures |
| `loader.py:60` | then-branch never taken | spec_from_file_location returns None only for a path whose suffix has no registered loader |
| `resolve.py:18` | then-branch never taken | typing |
| `results/calibration.py:76` | then-branch never taken | n = len(judges) at line 75, and line 66 already returned None when len(judges) < 2 |
| `results/calibration.py:121` | then-branch never taken | signed_bias has exactly one caller (calibration_analysis |
| `results/calibration_analysis.py:82` | except never raised | collect_human_labels is called once, at calibrate |

The 18 DEFENSIVE regions are kept on the same reasoning: each is
guarded by an invariant that holds today, and each would fire if that invariant
ever stopped holding — which is when you want it.

## §5 What this does not cover

The census measures whether a line runs, not whether it is right. The blockers
five audit rounds found were mostly in code that *was* covered and did the wrong
thing. Coverage is a floor, not a verdict.
