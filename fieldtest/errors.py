"""
fieldtest/errors.py

All exception types. All FieldtestError messages go to stderr.
Nothing on stdout when exiting 1.
"""


class FieldtestError(Exception):
    """Base. All fieldtest errors inherit from this."""
    pass


class ConfigError(FieldtestError):
    """Config is invalid, missing required fields, or references missing files.
    Raised at load/validate time. Always exit 1."""
    pass


class OutputError(FieldtestError):
    """Expected output files missing and --allow-partial not set.
    Raised during output validation. Always exit 1."""
    pass


class JudgeError(FieldtestError):
    """Judge LLM call failed (API error, timeout, parse failure).
    NOT raised — caught and returned as ResultRow(error=...) so run completes.
    Surfaced in report as unscored rows with error message."""
    pass


class ProviderError(FieldtestError):
    """Unknown or misconfigured provider.
    Raised at startup if provider not recognised. Always exit 1."""
    pass
