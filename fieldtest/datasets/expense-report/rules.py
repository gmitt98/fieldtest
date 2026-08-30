"""
Rule evals for the expense-report dataset.

A rule eval is plain Python: it gets the output text and the fixture's inputs,
and returns {"passed": bool, "detail": str}. No API call, no cost, no judge to
disagree with. Anything you can check deterministically belongs here rather
than in an LLM eval.

`inputs` holds the fixture's inputs with `file:` values already read, so
inputs["receipts"] is the CSV text and not its path.
"""
import re

from fieldtest import rule

# A table row: | R-1041 | 2026-10-03 | airfare | $412.60 | $412.60 |
ROW   = re.compile(r"^\|\s*(R-\d+)\s*\|.*\|\s*\$([\d,]+\.\d{2})\s*\|\s*$", re.M)
TOTAL = re.compile(r"Total reimbursable:\s*\$([\d,]+\.\d{2})")


def _money(s: str) -> float:
    return float(s.replace(",", ""))


@rule("total_matches_line_items")
def total_matches_line_items(output: str, inputs: dict) -> dict:
    """
    The stated total must equal the sum of the reimbursable column.

    This is the eval an LLM judge is worst at and a rule is best at. A judge
    reading a plausible-looking table tends to accept the total printed under
    it; addition either works or it does not.
    """
    rows = ROW.findall(output)
    if not rows:
        return {"passed": False, "detail": "no line items found in the output"}

    stated = TOTAL.search(output)
    if not stated:
        return {"passed": False, "detail": "no 'Total reimbursable: $N.NN' line"}

    line_sum = sum(_money(amount) for _, amount in rows)
    claimed  = _money(stated.group(1))
    ok = abs(line_sum - claimed) < 0.005
    return {
        "passed": ok,
        "detail": (
            f"line items sum to ${line_sum:.2f}, output states ${claimed:.2f}"
            if not ok else f"${claimed:.2f} matches {len(rows)} line items"
        ),
    }


@rule("excluded_categories_not_reimbursed")
def excluded_categories_not_reimbursed(output: str, inputs: dict) -> dict:
    """
    Nothing in policy 4.3 may be reimbursed at more than $0.00.

    Tagged `safe` rather than `right`: paying out an expense the policy forbids
    is not the same kind of mistake as adding up wrong, and separating them is
    the point of the taxonomy. Deterministic, so it costs nothing to run on
    every output on every commit.
    """
    excluded = {"alcohol", "entertainment", "fines", "personal"}
    offenders = []
    for line in output.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 6 or not cells[1].startswith("R-"):
            continue
        category, reimbursable = cells[3].lower(), cells[5]
        if category in excluded and reimbursable not in ("$0.00", "$0"):
            offenders.append(f"{cells[1]} ({category}) reimbursed {reimbursable}")

    return {
        "passed": not offenders,
        "detail": (
            "; ".join(offenders) if offenders
            else "no excluded category was reimbursed"
        ),
    }


# ---------------------------------------------------------------------------
# TODO — your turn.
#
# One of the bundled outputs contains a receipt ID that appears in none of the
# source receipts. Write the rule that catches it.
#
# inputs["receipts"] is the CSV text. Something like:
#
#     import csv, io                      # you will need these
#     known = {r["receipt_id"] for r in csv.DictReader(io.StringIO(inputs["receipts"]))}
#     cited = {rid for rid, _ in ROW.findall(output)}
#     invented = cited - known
#
# Then decide what `detail` should say. A detail that names the invented ID is
# worth more than one that says "failed", because the report shows detail and
# not much else.
#
# Register it by adding, in config.yaml:
#
#     - id: no_invented_receipts
#       tag: right
#       type: rule
#       description: every receipt cited exists in the source file
#
# @rule("no_invented_receipts")
# def no_invented_receipts(output: str, inputs: dict) -> dict:
#     ...
# ---------------------------------------------------------------------------
