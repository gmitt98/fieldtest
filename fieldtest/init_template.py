"""
fieldtest/init_template.py

Starter config.yaml content created by `fieldtest init`.
Comments are the onboarding document — they teach the reasoning, not just the syntax.
"""

STARTER_CONFIG = """\
# evals/config.yaml
# fieldtest configuration — this file IS your eval practice.
# Fill it out in order. Each section requires the thinking from the section above it.
# Run: fieldtest validate   (check this file)
# Run: fieldtest score      (evaluate outputs after running your system)

schema_version: 1

system:
  name: ""        # what is this system? (e.g. "Resume tailoring assistant")
  domain: ""      # Operational Design Domain — what inputs is it designed to handle?
                  # Be specific. This is the boundary of your eval coverage.
                  # Example: "English-language resumes tailored to job descriptions
                  #           for users with 2+ years experience"
                  # Everything outside this domain is out of scope by definition.

use_cases:
  - id: ""                  # snake_case identifier (e.g. tailor_resume)
    description: ""         # what is the user trying to accomplish?
                            # write this from the user's perspective, not the system's

    evals:
      # One eval per failure mode. Never bundle multiple checks into one eval.
      # Each eval gets a tag (right | good | safe) and a type (rule | regex | llm | reference).
      # You don't need all three tags — start with what matters most.
      # A suite with a single eval is a valid suite.
      #
      # RIGHT = correctness. Is the output factually/logically correct?
      #         Failure here → grounding, retrieval, or reasoning problem.
      # GOOD  = quality. Is the output well-made for its purpose?
      #         Failure here → prompt engineering, format, or style problem.
      # SAFE  = guardrails. Does it respect hard limits?
      #         Failure here → architectural problem, not a prompt problem.

      - id: ""
        tag: right          # right | good | safe
        type: llm           # rule | regex | llm | reference
        description: ""     # what specific thing does this eval check?
                            # complete this sentence: "This eval checks whether the output..."

        # For type: llm (binary — default)
        pass_criteria: ""   # what does a passing output look like?
        fail_criteria: ""   # what does a failing output look like?

      # - id: ""
      #   tag: safe
      #   type: regex
      #   description: ""
      #   pattern: ""       # regex pattern
      #   match: false      # false = output must NOT match this pattern

      # - id: ""
      #   tag: good
      #   type: reference
      #   description: ""
      #   # expected values live in the fixture file (fixtures/golden/*.yaml)

    fixtures:
      directory: fixtures/
      sets:
        smoke:      []      # list a few fixture IDs for fast iteration
        regression: golden/*  # all golden fixtures — run on every change
        full:       all     # everything
      runs: 5               # how many times to run each fixture
                            # higher = more stable distributions, more cost
                            # 5 is a reasonable start; increase for high-variance evals

# Global defaults for judge LLM — NOT your system's model
# The eval tool makes its own LLM calls to score outputs.
# Providers: anthropic (set ANTHROPIC_API_KEY) or openai (set OPENAI_API_KEY)
# For OpenAI: pip install fieldtest[openai]
defaults:
  provider: anthropic       # anthropic | openai
  model: claude-sonnet-4-20250514   # anthropic: claude-sonnet-4-20250514, claude-haiku-3-5-20251001
                            # openai: gpt-4o, gpt-4o-mini
  runs: 5
"""

GITIGNORE_CONTENT = "outputs/\n"
