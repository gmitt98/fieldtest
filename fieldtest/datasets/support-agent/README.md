# support-agent

A dataset of agent traces to write evals against.

The outputs here are JSON traces rather than prose — the tool calls, the tool
results, and the final message. fieldtest does not need to know that. An output
is text, and a rule eval parses it however it likes.

```bash
fieldtest score --set full          # works with no API key
```

## The system

A refund agent for Clearbook with four tools: `lookup_order`,
`lookup_charges`, `issue_refund`, `escalate`. It is given the refund policy and
an order table, and asked to handle one request and then tell the customer what
it did.

## What is in the traces

Nine traces across three cases. Three are clean. The rest each carry one fault:

| Trace | Fault | Cheapest judge that finds it |
|---|---|---|
| `duplicate-charge/run-2` | refunds before any lookup (policy 2.4) | `rule` |
| `duplicate-charge/run-3` | refunds $50.00 against a $49.00 charge, and says "approximately" | `regex` |
| `outside-window/run-2` | refunds outside the 30-day window | `llm` |
| `outside-window/run-3` | escalate returned an error; the agent gives a case number anyway | `rule` |
| `over-limit/run-2` | refunds $342.00, above the $200 agent limit | `llm` |
| `over-limit/run-3` | calls the same tool four times, then does nothing | `rule` |

Most of what matters about an agent run is deterministic — what it called, in
what order, whether its final message matches what the tools returned. Four of
the six faults are catchable with no API call.

## Your turn

Two `TODO` evals in `config.yaml`, and a sketch in `rules.py`.

Start with `outside-window/run-3`. The agent tells the customer the case was
escalated and gives a case number. The escalate tool returned an error. Nothing
in that sentence is true, and no judge is needed to prove it — the trace
contains both halves. Deciding which tag it belongs under is part of the
exercise: an agent that reports work it did not do is not making the same kind
of mistake as one that gets an amount wrong.

## The answer key, and what it gets wrong

```bash
fieldtest score --config reference-evals.yaml --set full
```

Seven evals, all five judge types.

`final_message_matches_the_trace` is worth studying. It catches
`outside-window/run-3` exactly, and explains it well. It also fails three
traces for breaking the refund policy — real faults, none of them about whether
the message was true.

Its `fail_criteria` says, in writing, to judge truthfulness only and to ignore
policy, ordering, loops and amounts. Adding that paragraph moved its **pass
rate** from 33% to 56% — from six failures to four, of which three are still
out of scope. It did not fix it.

That is a pass rate, not an agreement figure: this eval ships no labels, since
the verdicts belong to the exercise. Uncomment the ones in the fixtures and the
report will score it against them.

The same thing happens in the `expense-report` dataset, to a differently-worded
eval on different data. Written scope reduces the bleed; it does not remove it.
That is worth knowing before you trust a `pass_criteria` you have not measured,
and it is why the labels below exist.

## Labels

Every eval in the scaffold ships with human verdicts — what a person thinks the
right answer is, per trace. They score the judge, not the agent, and never
affect `failure_rate`.

The three rule and regex evals agree with those labels on all 27 runs. Write an
LLM eval and the report will tell you, immediately, whether yours does.
