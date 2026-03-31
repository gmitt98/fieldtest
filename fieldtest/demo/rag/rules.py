from fieldtest import rule


@rule("answer-length")
def check_answer_length(output: str, inputs: dict) -> dict:
    """Answer must be between 20 and 250 words."""
    words = len(output.split())
    if words < 20:
        return {"passed": False, "detail": f"too short: {words} words (minimum 20)"}
    if words > 250:
        return {"passed": False, "detail": f"too long: {words} words (maximum 250)"}
    return {"passed": True, "detail": f"{words} words — within range"}
