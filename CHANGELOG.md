# Changelog

Notable changes to fieldtest. Each entry describes what you can now do, or what stopped
going wrong — not what commits landed.

## Unreleased

### Your judge now holds still between runs

Judges previously ran at whatever sampling temperature the provider defaulted to, which for
most providers is 1.0. That meant the `stddev` on a scored eval and the `failure_rate` on a
binary eval both moved between runs for reasons that had nothing to do with the system you
were measuring, and nothing in the report told you which was which.

The judge now runs at temperature 0.0 unless you say otherwise. Score the same `outputs/`
directory twice and you should get the same answer twice.

**Your numbers will move when you upgrade.** That movement is noise being removed, not a
regression in your system. If you want the old behaviour, set it explicitly:

```yaml
defaults:
  judge_temperature: 1.0
```

`defaults.judge_seed` is also available for providers that support it. Where a provider does
not support a parameter you asked for — Anthropic has no seed — fieldtest drops it, finishes
the run, and says so once in the report header instead of failing.

Gemini judges were also previously unbounded in output length, and are now capped like the
others.
