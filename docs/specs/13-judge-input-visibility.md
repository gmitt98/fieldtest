# Spec 13 — Judge input visibility

**Tier** 1 · **Depends on** 03 · **Touches** `judges/llm.py`, `judges/dispatch.py`, `config.py`, recipes · **Status** draft

## §1 Problem

`build_binary_judge_prompt(eval, output)` and `build_scored_judge_prompt(eval, output)` take the
eval and the output. They never receive the fixture. Only rule evals get one — `dispatch_judge`
passes `fixture.get("inputs", {})` to the registered function and nothing else.

So an LLM judge is shown a reply and asked to rule on it, with no access to what the system was
replying to.

For a tone or format eval that is fine; the output is the whole subject. For anything comparative
it is not, and fieldtest's own published recipes are full of comparative criteria.
`docs/recipes/rag-faithfulness.md` recommends:

```yaml
pass_criteria: All specific claims can be traced to the retrieved excerpt
pass_criteria: Every specific detail can be found in the context
```

The retrieved excerpt lives in `fixture.inputs.context`. The judge never sees it. The tool ships a
recipe asking a question it structurally cannot answer.

This surfaced when the bundled demo results were regenerated against a current judge. Two rag
evals moved from 0.167 to 0.818, and the reasoning says exactly why:

> The output makes specific claims about expense thresholds ($75, $500), approval requirements,
> and a 30-day deadline, but no handbook excerpt was provided to verify these details against.

The judge is correct and the tool is wrong. The earlier 0.167 was a different judge being less
scrupulous about admitting it could not check — and which judge that was is unrecoverable,
because the run predates the provenance block spec 01 added.

The cost is not a wrong number. It is a wrong number that looks right: a grounding eval that
cannot see the source still returns pass and fail, and a user reading `failure_rate: 0.167` has no
way to tell that the judge was guessing.

## §2 Requirements

1. LLM judges receive the fixture inputs alongside the output.
2. Inputs are untrusted data and are delimited and neutralized exactly as the output already is
   under spec 03. A fixture is as capable of carrying an injection as an output, and more likely
   to, since adversarial fixtures are the documented use case.
3. A fixture with no `inputs` block produces a prompt byte-identical to today's, so evals that do
   not need inputs are unaffected and their historical results stay comparable.
4. Inputs appear before the output in the prompt. The judge reads the question before the answer,
   which is the order the task is stated in.
5. An eval may opt out. Some evals are deliberately about the output alone, and a long retrieved
   context is expensive to send on every judge call.
6. Prompt shape stays deterministic: two builders reading this spec produce identical bytes for
   the same input, as spec 03 already requires.
7. The judge fingerprint changes when input visibility changes, because a judge shown the context
   is not the same instrument as one that is not.

## §3 Contract

```python
def build_binary_judge_prompt(
    eval: Eval, output: str, inputs: Optional[dict] = None
) -> str:
```

Rendered section, present only when inputs exist and the eval has not opted out:

```
System input:
---
question: What is the expense reimbursement limit?
context: Employees may expense meals up to $75 without approval...
---

Output to evaluate:
---
{output}
---
```

Keys are rendered sorted, one per line, `key: value`, with multi-line values indented under the
key. Sorted because prompt bytes must not depend on YAML key order, which is preserved by the
parser and therefore variable across fixture files.

`Eval` gains:

```python
judge_sees_inputs: bool = True
```

Default true: the failure this spec corrects is silent, and a user who has not thought about it
is better served by a judge that can see. A user who has thought about it, and wants the output
judged alone or wants to keep a large context out of every call, sets it false.

The spec 01 fingerprint payload gains the set of evals with `judge_sees_inputs: false`, so a run
where the judge was blinded is not auto-compared against one where it was not.

## §4 Compatibility

This changes judge prompts, so it changes results for every eval whose fixture has inputs. That is
the point, and it must be called out in the changelog rather than shipped quietly: rates will
move, and the direction is not predictable — a judge that can finally see the context may pass
answers it was failing, or fail answers it was passing on vibes.

`find_baseline()` already refuses to compare across judge fingerprints, and §2.7 puts opt-outs in
the fingerprint, but the first run after upgrading compares against a pre-upgrade baseline whose
fingerprint has no input-visibility component. Treat that comparison as suspect for one run.

Fixtures without `inputs` are unaffected, byte for byte.

## §5 Acceptance

Tests in `tests/test_judges.py` and `tests/test_integration.py`:

- `test_prompt_unchanged_when_fixture_has_no_inputs`
- `test_inputs_rendered_before_the_output`
- `test_input_keys_rendered_in_sorted_order`
- `test_multiline_input_values_stay_readable`
- `test_delimiters_in_inputs_are_neutralized`
- `test_eval_can_opt_out_of_seeing_inputs`
- `test_opt_out_changes_the_judge_fingerprint`
- `test_scored_judge_sees_inputs_too`
- `test_rag_grounding_eval_can_reach_its_context` (integration)

Behavioral acceptance: rescore the rag demo. `no-hallucination` and `answers-from-context` are
currently at 0.818 with the judge stating it was given no context; after this they must be judged
on the context that was there all along. Whatever the rate becomes is the real one — the test is
that the reasoning stops saying the source was missing.

## §6 Out of scope

Retrieval itself. fieldtest does not fetch context; whatever the generator recorded in the fixture
is what the judge sees, which keeps the generator-writes-files contract intact.

Also out of scope: truncating long inputs. A context too large for a judge call is a real problem
and a separate one, and silently sending half a document would reintroduce exactly the failure
this spec exists to remove — a judge ruling on evidence it does not have.
