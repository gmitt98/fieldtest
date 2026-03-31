from fieldtest import rule


@rule("has-greeting")
def check_has_greeting(output: str, inputs: dict) -> dict:
    """Reply must open with a recognizable greeting."""
    greetings = ["dear ", "hello", "hi ", "thank you for contacting", "thank you for reaching"]
    first_100 = output.strip().lower()[:100]
    passed = any(g in first_100 for g in greetings)
    return {
        "passed": passed,
        "detail": "greeting found" if passed else "no greeting in first 100 chars",
    }
