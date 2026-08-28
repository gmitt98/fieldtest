"""
tests/conftest.py

A skipped live test looks exactly like a passing one.

That is the failure mode spec 12 was written against: four defects reached a
release through a suite that could not ask the provider anything. A live tier
that silently skips half its providers reproduces the same blind spot in a
quieter form — `2 skipped` scrolls past, and the run reads as verification.

So `pytest -m live` ends with an explicit report of which providers were
exercised and which were not, and exits non-zero if none were. Skipping is
still allowed per provider, because nobody has accounts everywhere. Skipping
silently is not.
"""
from __future__ import annotations

import os

import pytest

# Provider → the environment variable its live tests require.
#
# Derived from the table `fieldtest validate` reports rather than restated. A
# second copy is how the gemini entry came to say GOOGLE_API_KEY while the
# adapter read GEMINI_API_KEY, and this file would have inherited the same lie.
#
# openai_compatible is the one addition: it names its credential in config, not
# in a built-in table, so the live tier picks one for its default endpoint.
def _live_credentials() -> dict[str, str]:
    from fieldtest.cli_common import _PROVIDER_ENV

    return {**_PROVIDER_ENV, "openai_compatible": "OPENROUTER_API_KEY"}


LIVE_CREDENTIALS = _live_credentials()


def _live_selected(config) -> bool:
    """True when this run is actually asking for the live tier."""
    expr = config.getoption("-m") or ""
    return "live" in expr and "not live" not in expr


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _live_selected(config):
        return

    tr = terminalreporter
    configured = {p: v for p, v in LIVE_CREDENTIALS.items() if os.environ.get(v)}
    missing = {p: v for p, v in LIVE_CREDENTIALS.items() if not os.environ.get(v)}

    tr.write_sep("=", "live tier coverage")
    for provider, var in sorted(configured.items()):
        tr.write_line(f"  exercised   {provider:<18} {var}")
    for provider, var in sorted(missing.items()):
        tr.write_line(f"  NOT TESTED  {provider:<18} {var} is unset")

    if missing:
        tr.write_line("")
        tr.write_line(
            f"  {len(missing)} of {len(LIVE_CREDENTIALS)} providers went untested. "
            f"Their adapters are unverified against a real API by this run."
        )

    if not configured:
        tr.write_line("")
        tr.write_line("  No provider credentials were set — this run verified nothing.")
        # Exit non-zero: a live run that asked no provider anything must not
        # report success, or `pytest -m live` becomes a no-op that passes.
        terminalreporter._session.exitstatus = pytest.ExitCode.USAGE_ERROR


def pytest_sessionfinish(session, exitstatus):
    """Enforce the exit code set above; terminal_summary runs too late for it."""
    if not _live_selected(session.config):
        return
    if not any(os.environ.get(v) for v in LIVE_CREDENTIALS.values()):
        session.exitstatus = pytest.ExitCode.USAGE_ERROR
