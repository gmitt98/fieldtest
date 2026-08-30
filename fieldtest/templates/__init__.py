"""
fieldtest/templates/

Curated config templates for common AI product types.
Each template provides pre-populated eval configs with judge prompts
that users customize for their specific system.
"""
from pathlib import Path


def available_templates() -> list[str]:
    """Template names, from the YAML files that actually ship."""
    return sorted(p.stem for p in Path(__file__).parent.glob("*.yaml"))


# Derived rather than written out: this was a hardcoded list that nothing read,
# duplicating the same three names in cli_project.py's click.Choice.
AVAILABLE_TEMPLATES = available_templates()
