import json
from fieldtest import rule

REQUIRED_FIELDS = ["vendor", "invoice_number", "amount", "due_date"]


@rule("valid-json")
def check_valid_json(output: str, inputs: dict) -> dict:
    """Output must be valid, parseable JSON."""
    try:
        json.loads(output.strip())
        return {"passed": True, "detail": "valid JSON"}
    except json.JSONDecodeError as e:
        return {"passed": False, "detail": f"JSON parse error: {e}"}


@rule("required-fields-present")
def check_required_fields(output: str, inputs: dict) -> dict:
    """All required fields must be present in the JSON output."""
    try:
        data = json.loads(output.strip())
    except json.JSONDecodeError:
        return {"passed": False, "detail": "cannot check fields — output is not valid JSON"}

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        return {"passed": False, "detail": f"missing fields: {', '.join(missing)}"}
    return {"passed": True, "detail": f"all required fields present: {', '.join(REQUIRED_FIELDS)}"}
