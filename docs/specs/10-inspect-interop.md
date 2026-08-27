# Spec 10 — Inspect interop

**Tier** 3 · **Depends on** 01 · **Touches** new `interop/` module, optional dependency · **Status** sketch

## §1 Rationale

Inspect (UK AI Security Institute) is the deepest execution runtime in open-source evals: 20+
model providers, sandboxing, agents including bridges to external coding agents, MCP tool
support, and over 200 prebuilt benchmark implementations. It has no forced failure taxonomy.
Scorers emit scores, metrics aggregate them, and nothing requires a user to declare whether a
failure is correctness, quality, or safety.

That is the exact gap fieldtest fills, and the two are not competitors. Inspect runs systems.
fieldtest classifies failures. The generator-writes-files contract means fieldtest never needed a
runtime, which also means it can accept outputs from any runtime, including theirs.

Interop is the distribution play. It puts the right/good/safe taxonomy in front of an existing
user base rather than asking that user base to adopt a second framework.

## §2 Two directions

**Inbound: `fieldtest score --from-inspect <log.eval>`**

Read an Inspect eval log, extract each sample's input and model output, and materialize them into
the fieldtest layout: `outputs/{sample_id}/run-N.txt` plus generated fixture YAML carrying the
sample input. Then score normally.

Straightforward, since Inspect exposes `read_eval_log()` and a dataframe API. The mapping
questions are sample id to fixture id, and how epochs map to `runs` (they map cleanly: an Inspect
epoch is a repeated generation, which is what `runs` means here).

**Outbound: a fieldtest scorer package for `inspect score --scorer`**

Inspect's `inspect score` accepts a scorer from a package or a source file, and the `--action
append` mode adds scores alongside existing ones without discarding them. A fieldtest scorer
would read the right/good/safe config, apply the four eval types to the log's samples, and write
tagged scores back into the Inspect log.

This is the higher-leverage direction and the harder one. It means the taxonomy lands inside
their log format and their viewer, and an Inspect user gets tagged diagnostics without leaving
their workflow.

## §3 Open questions

1. Does the outbound scorer write one Inspect scorer per fieldtest eval, or one scorer emitting
   structured multi-dimensional scores? The former composes with their existing metrics; the
   latter preserves the tag structure. Probably the former, with the tag carried in scorer
   metadata.
2. Does fieldtest take `inspect-ai` as an optional dependency, matching the existing
   `fieldtest[openai]` and `fieldtest[gemini]` pattern, or ship the scorer as a separate package
   in their extensions gallery? The gallery is the discovery surface, which argues for separate.
3. Inbound conversion loses the transcript. An Inspect agent run is a multi-turn trajectory and
   `run-N.txt` is a flat string. Decide whether to flatten, take the final message only, or
   decline agent logs at the boundary with a clear error.
4. Judge provenance (spec 01) has to survive the round trip in both directions, otherwise the
   interop reintroduces the defect that spec 01 fixes.

## §4 Sequencing

Nothing here before tier 1 and tier 2 ship. Interop with a tool whose judge is unversioned and
unpinned exports the defect rather than the capability. Inbound first, since it is smaller and
proves the mapping before committing to their scorer API.
