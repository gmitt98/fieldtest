"""
fieldtest/results/html.py

write_html() — generates a self-contained HTML eval report.
Single file, all CSS/JS inline, no external dependencies.
"""
from __future__ import annotations

import json
from pathlib import Path


def write_html(run_data: dict, config, output_path: Path) -> None:
    """
    Write a self-contained HTML eval report to output_path.

    run_data keys: run_id, set, fixture_count, runs, rows, summary, delta
    config: fieldtest Config model (used for use_case descriptions and eval metadata)
    """
    html = _build_html(run_data, config)
    output_path.write_text(html, encoding="utf-8")


def _build_html(run_data: dict, config) -> str:
    run_id        = run_data.get("run_id", "—")
    set_name      = run_data.get("set", "—")
    fixture_count = run_data.get("fixture_count", 0)
    runs          = run_data.get("runs", 0)
    rows          = run_data.get("rows", [])
    summary       = run_data.get("summary", {})
    delta         = run_data.get("delta", {})
    judge         = run_data.get("judge") or {}
    judge_runs    = run_data.get("judge_runs", 1)

    # Judge errors shrink the sample instead of failing the run — say so up top.
    from fieldtest.results.aggregator import summarize_judge_errors
    judge_errors = summarize_judge_errors(summary)
    if judge_errors:
        affected_str = ", ".join(
            f"{eval_id} ({scored} of {attempted} runs scored)"
            for eval_id, scored, attempted in judge_errors["affected"]
        )
        judge_error_banner = (
            '<div class="judge-errors">'
            f"⚠ judge errors: {judge_errors['failed']} of {judge_errors['total']} "
            f"calls failed after retry. Affected evals: {affected_str}"
            "</div>"
        )
    else:
        judge_error_banner = ""

    # A partial run reports its rates over fewer outputs than the header's
    # "Runs/fixture" implies. The markdown said so and this page did not, so a
    # reader comparing the two saw the same numbers with only one caveat.
    if run_data.get("partial"):
        details = run_data.get("partial_details") or []
        shown   = ", ".join(details[:6])
        more    = f" (+{len(details) - 6} more)" if len(details) > 6 else ""
        partial_banner = (
            '<div class="partial-run">'
            f"⚠ partial run: {len(details)} output(s) missing and skipped"
            + (f" — {shown}{more}" if details else "")
            + ". Rates below are over the outputs that were found."
            "</div>"
        )
    else:
        partial_banner = ""

    # Extract timestamp from run_id: 2026-03-22T14-30-00-a3f9
    try:
        ts_part    = run_id[:19].replace("T", " ").replace("-", ":")
        date_part, time_part = ts_part.split(" ")
        timestamp  = f"{date_part.replace(':', '-')} {time_part[:5]}"
    except Exception:
        timestamp  = run_id

    # Compute tag health summary across all use cases
    # Binary evals → pass rate. Scored evals → avg score (shown separately).
    tag_health: dict[str, dict] = {
        "right": {"passed": 0, "total": 0, "scores": []},
        "good":  {"passed": 0, "total": 0, "scores": []},
        "safe":  {"passed": 0, "total": 0, "scores": []},
    }
    for r in rows:
        if r.get("skipped") or r.get("error"):
            continue
        tag = (r.get("tag") or "").lower()
        if tag in tag_health:
            if r.get("score") is not None:
                tag_health[tag]["scores"].append(r["score"])
            else:
                tag_health[tag]["total"] += 1
                if r.get("passed"):
                    tag_health[tag]["passed"] += 1

    def _pct(d: dict) -> str:
        if d["total"] == 0 and not d["scores"]:
            return "—"
        if d["total"] == 0:
            # All scored, no binary — show avg
            avg = round(sum(d["scores"]) / len(d["scores"]), 1)
            return f"avg {avg}"
        pct = f"{round(d['passed'] / d['total'] * 100)}%"
        if d["scores"]:
            avg = round(sum(d["scores"]) / len(d["scores"]), 1)
            return f"{pct} + avg {avg}"
        return pct

    right_pct = _pct(tag_health["right"])
    good_pct  = _pct(tag_health["good"])
    safe_pct  = _pct(tag_health["safe"])

    # Build use_case eval metadata maps
    uc_eval_meta: dict[str, dict] = {}   # uc_id -> {eval_id -> {tag, labels}}
    for uc in config.use_cases:
        uc_eval_meta[uc.id] = {
            ev.id: {"tag": ev.tag, "labels": ev.labels}
            for ev in uc.evals
        }

    # Build delta section HTML
    n_uc = len(config.use_cases)
    uc_note = f" across {n_uc} use cases" if n_uc > 1 else ""
    scope_note = (
        '<p style="font-size:12px;color:#888;margin-bottom:8px;">'
        f"Rates below cover all {n_uc} use cases. Each use case reports its own "
        "below.</p>"
        if n_uc > 1 else ""
    )

    judge_meta = ""
    if judge:
        temp = judge.get("temperature")
        bits = [f"{judge.get('provider', '?')}/{judge.get('model', '?')}"]
        if temp is not None:
            bits.append(f"temp {temp}")
        if judge_runs > 1:
            bits.append(f"judged {judge_runs}× each")
        if judge.get("fingerprint"):
            bits.append(judge["fingerprint"])
        judge_meta = (
            '<div class="meta">Judge: <span>' + " · ".join(bits) + "</span></div>"
        )

    delta_html = _build_delta_html(delta)

    # Build use_case sections HTML
    uc_sections_html = ""
    for uc in config.use_cases:
        uc_rows = [r for r in rows if r.get("use_case") == uc.id]
        if not uc_rows:
            continue
        uc_sections_html += _build_uc_section(uc, uc_rows, summary.get(uc.id, {}))

    # Serialize run_data for JS (convert lists to ensure JSON-safe)
    run_data_json = json.dumps(run_data, default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>fieldtest — {run_id}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    color: #1a1a1a;
    background: #f5f5f5;
    line-height: 1.5;
  }}
  .header {{
    background: #1a1a1a;
    color: #fff;
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 24px;
    flex-wrap: wrap;
  }}
  .header .brand {{ font-size: 18px; font-weight: 700; letter-spacing: -0.5px; color: #fff; }}
  .header .meta {{ font-size: 13px; color: #aaa; }}
  .header .meta span {{ color: #fff; font-weight: 500; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  .judge-errors {{
    max-width: 1200px; margin: 16px auto 0; padding: 12px 16px;
    background: #3a2a1a; border-left: 3px solid #d68b2c;
    color: #f0d5b0; font-size: 13px; border-radius: 3px;
  }}
  .tag-health {{
    display: flex;
    gap: 16px;
    margin-bottom: 24px;
    flex-wrap: wrap;
  }}
  .tag-box {{
    background: #fff;
    border-radius: 8px;
    padding: 16px 24px;
    border: 1px solid #e0e0e0;
    min-width: 140px;
    text-align: center;
  }}
  .tag-box .tag-name {{ font-size: 11px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #888; margin-bottom: 4px; }}
  .tag-box .tag-rate {{ font-size: 32px; font-weight: 700; }}
  .tag-box.right .tag-rate  {{ color: #2e7d32; }}
  .tag-box.good  .tag-rate  {{ color: #1565c0; }}
  .tag-box.safe  .tag-rate  {{ color: #c62828; }}
  .uc-section {{
    background: #fff;
    border-radius: 8px;
    border: 1px solid #e0e0e0;
    margin-bottom: 24px;
    overflow: hidden;
  }}
  .uc-header {{
    padding: 16px 20px;
    border-bottom: 1px solid #eee;
    background: #fafafa;
  }}
  .uc-header h2 {{ font-size: 16px; font-weight: 700; }}
  .uc-header p {{ font-size: 13px; color: #666; margin-top: 2px; }}
  .label-bar {{
    padding: 12px 20px;
    border-bottom: 1px solid #eee;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }}
  .label-bar .bar-title {{ font-size: 12px; color: #888; font-weight: 600; margin-right: 4px; }}
  .label-chip {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid #ccc;
    background: #f0f0f0;
    color: #444;
    transition: background 0.15s, color 0.15s;
    user-select: none;
  }}
  .label-chip.active {{
    background: #1a1a1a;
    color: #fff;
    border-color: #1a1a1a;
  }}
  .judge-block {{ margin-top: 24px; }}
  .judge-block h3 {{
    font-size: 14px; font-weight: 600; color: #1a1a1a;
    margin-bottom: 8px; letter-spacing: -0.2px;
  }}
  .judge-note {{ font-size: 12px; color: #666; margin-top: 8px; }}
  .judge-low {{ color: #c0392b; font-weight: 600; }}
  .matrix-wrap {{
    overflow-x: auto;
    padding: 0 0 4px 0;
  }}
  table.matrix {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  table.matrix th, table.matrix td {{
    padding: 8px 12px;
    text-align: center;
    border-bottom: 1px solid #f0f0f0;
    white-space: nowrap;
  }}
  table.matrix th {{
    font-weight: 600;
    font-size: 12px;
    letter-spacing: 0.3px;
    background: #fafafa;
    border-bottom: 2px solid #e0e0e0;
  }}
  table.matrix th.fixture-col {{ text-align: left; }}
  table.matrix tfoot td {{
    font-size: 11px; color: #555; background: #fafafa;
    border-top: 2px solid #ddd; white-space: nowrap;
  }}
  table.matrix tfoot td.fixture-col {{ text-align: left; font-weight: 600; }}
  table.matrix td.fixture-cell {{
    text-align: left;
    font-weight: 500;
    color: #444;
    font-family: monospace;
    font-size: 12px;
  }}
  table.matrix th.right-col {{ background: #e8f5e9; color: #2e7d32; }}
  table.matrix th.good-col  {{ background: #e3f2fd; color: #1565c0; }}
  table.matrix th.safe-col  {{ background: #ffebee; color: #c62828; }}
  .cell-pass  {{ background: #e8f5e9; color: #1b5e20; font-weight: 600; cursor: pointer; }}
  .cell-fail  {{ background: #ffebee; color: #b71c1c; font-weight: 600; cursor: pointer; }}
  .cell-error {{ background: #fff8e1; color: #e65100; font-weight: 600; cursor: pointer; }}
  .cell-skip  {{ background: #fafafa; color: #aaa; }}
  .cell-pass:hover, .cell-fail:hover, .cell-error:hover {{ opacity: 0.85; }}
  .detail-panel {{
    display: none;
    padding: 12px 20px;
    border-top: 1px solid #f0f0f0;
    background: #fafafa;
  }}
  .detail-panel.open {{ display: block; }}
  .detail-panel h4 {{ font-size: 13px; font-weight: 700; margin-bottom: 8px; color: #333; }}
  .run-detail {{
    margin-bottom: 8px;
    padding: 8px 12px;
    border-radius: 6px;
    border-left: 3px solid #ccc;
    background: #fff;
    font-size: 13px;
  }}
  .run-detail.pass {{ border-left-color: #4caf50; }}
  .run-detail.fail {{ border-left-color: #f44336; }}
  .run-detail.error {{ border-left-color: #ff9800; }}
  .run-detail .run-badge {{ font-weight: 700; margin-right: 6px; }}
  .run-detail .run-text  {{ color: #555; margin-top: 2px; font-size: 12px; }}
  .delta-section {{
    background: #fff;
    border-radius: 8px;
    border: 1px solid #e0e0e0;
    margin-bottom: 24px;
    padding: 16px 20px;
  }}
  .delta-section h2 {{ font-size: 15px; font-weight: 700; margin-bottom: 12px; color: #333; }}
  .delta-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .delta-table th {{ text-align: left; padding: 6px 10px; font-weight: 600; color: #888; font-size: 12px; border-bottom: 1px solid #eee; }}
  .delta-table td {{ padding: 6px 10px; border-bottom: 1px solid #f5f5f5; }}
  .partial-run {{ background: #fdf6e3; border-bottom: 1px solid #e8d9a8;
                  color: #8a6d1f; font-size: 13px; padding: 10px 24px; }}
  .delta-caveat {{ font-size: 12px; color: #8a6d1f; background: #fdf6e3;
                   border-left: 3px solid #d9a441; padding: 8px 10px;
                   margin: 0 0 10px; line-height: 1.5; }}
  .delta-up   {{ color: #2e7d32; font-weight: 600; }}
  .delta-down {{ color: #c62828; font-weight: 600; }}
  .no-change  {{ color: #888; }}
  .hidden-col {{ display: none; }}
</style>
</head>
<body>

<div class="header">
  <span class="brand">fieldtest</span>
  <div class="meta">Run: <span>{run_id}</span></div>
  <div class="meta">Time: <span>{timestamp}</span></div>
  <div class="meta">Set: <span>{set_name}</span></div>
  <div class="meta">Fixtures: <span>{fixture_count}</span>{uc_note}</div>
  <div class="meta">Runs/fixture: <span>{runs}</span></div>
  {judge_meta}
</div>

{judge_error_banner}
{partial_banner}

<div class="container">

  {scope_note}
  <div class="tag-health">
    <div class="tag-box right">
      <div class="tag-name">RIGHT</div>
      <div class="tag-rate">{right_pct}</div>
    </div>
    <div class="tag-box good">
      <div class="tag-name">GOOD</div>
      <div class="tag-rate">{good_pct}</div>
    </div>
    <div class="tag-box safe">
      <div class="tag-name">SAFE</div>
      <div class="tag-rate">{safe_pct}</div>
    </div>
  </div>

  {uc_sections_html}

  {delta_html}

</div>

<script>
const RUN_DATA = {run_data_json};

// ---------------------------------------------------------------------------
// Label filter logic
// Each use_case section has a label-bar with chips. Clicking a chip toggles
// a label filter — columns whose eval has that label are shown; others hidden.
// "All" chip clears filter and shows everything.
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", function() {{
  document.querySelectorAll(".uc-section").forEach(function(section) {{
    const ucId = section.dataset.uc;
    const chips = section.querySelectorAll(".label-chip");
    const table = section.querySelector("table.matrix");

    // Track active label filter for this section (null = all)
    let activeLabel = null;

    chips.forEach(function(chip) {{
      chip.addEventListener("click", function() {{
        const label = chip.dataset.label;
        if (label === "__all__" || label === activeLabel) {{
          // Clear filter
          activeLabel = null;
          chips.forEach(c => c.classList.remove("active"));
          chip.classList.remove("active");
          // Show all columns (except fixture col)
          if (table) {{
            table.querySelectorAll("th[data-eval-id], td[data-eval-id]").forEach(function(el) {{
              el.classList.remove("hidden-col");
            }});
          }}
        }} else {{
          activeLabel = label;
          chips.forEach(c => c.classList.remove("active"));
          chip.classList.add("active");
          // Show only columns that have this label
          if (table) {{
            table.querySelectorAll("th[data-eval-id]").forEach(function(th) {{
              const labels = (th.dataset.labels || "").split("|").filter(Boolean);
              if (labels.includes(label)) {{
                th.classList.remove("hidden-col");
              }} else {{
                th.classList.add("hidden-col");
              }}
            }});
            table.querySelectorAll("td[data-eval-id]").forEach(function(td) {{
              const evalId = td.dataset.evalId;
              const th = table.querySelector('th[data-eval-id="' + evalId + '"]');
              if (th && th.classList.contains("hidden-col")) {{
                td.classList.add("hidden-col");
              }} else {{
                td.classList.remove("hidden-col");
              }}
            }});
          }}
        }}
      }});
    }});
  }});

  // ---------------------------------------------------------------------------
  // Cell detail panel toggle
  // ---------------------------------------------------------------------------
  document.querySelectorAll("td.cell-pass, td.cell-fail, td.cell-error").forEach(function(td) {{
    td.addEventListener("click", function() {{
      const fixtureId = td.dataset.fixtureId;
      const evalId    = td.dataset.evalId;
      const ucId      = td.closest(".uc-section").dataset.uc;
      const panelId   = "panel-" + ucId + "-" + fixtureId + "-" + evalId;
      let panel       = document.getElementById(panelId);

      // Close any other open panels
      document.querySelectorAll(".detail-panel.open").forEach(function(p) {{
        if (p.id !== panelId) p.classList.remove("open");
      }});

      if (!panel) {{
        // Build panel dynamically from RUN_DATA
        const matchRows = (RUN_DATA.rows || []).filter(function(r) {{
          return r.use_case === ucId && r.eval_id === evalId && r.fixture_id === fixtureId;
        }});
        panel = document.createElement("div");
        panel.className = "detail-panel";
        panel.id = panelId;

        let innerHtml = '<h4>' + evalId + ' / ' + fixtureId + '</h4>';
        matchRows.sort(function(a, b) {{ return a.run - b.run; }}).forEach(function(r) {{
          let cls, badge;
          if (r.error) {{
            cls = "error"; badge = "ERR";
          }} else if (r.score != null) {{
            cls = "pass"; badge = "SCORE " + r.score;
          }} else if (r.passed) {{
            cls = "pass"; badge = "PASS";
          }} else {{
            cls = "fail"; badge = "FAIL";
          }}
          let text  = r.error || r.detail || "";
          innerHtml += '<div class="run-detail ' + cls + '">';
          innerHtml += '<span class="run-badge">Run ' + r.run + ' — ' + badge + '</span>';
          if (text) innerHtml += '<div class="run-text">' + _esc(text) + '</div>';
          innerHtml += '</div>';
        }});
        if (!matchRows.length) {{
          innerHtml += '<p style="color:#888;font-size:12px;">No detail available.</p>';
        }}
        panel.innerHTML = innerHtml;

        // Insert after the table's parent row — insert after matrix-wrap div
        const matrixWrap = td.closest(".matrix-wrap");
        matrixWrap.parentNode.insertBefore(panel, matrixWrap.nextSibling);
      }}

      panel.classList.toggle("open");
    }});
  }});
}});

function _esc(str) {{
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}}
</script>

</body>
</html>"""


def _build_uc_section(uc, uc_rows: list[dict], uc_summary: dict) -> str:
    """Build HTML for a single use-case section."""
    uc_id = uc.id

    # Collect unique labels across all evals in this use case
    all_labels: list[str] = []
    for ev in uc.evals:
        for lbl in ev.labels:
            if lbl not in all_labels:
                all_labels.append(lbl)

    # Build label chip bar
    label_bar_html = ""
    if all_labels:
        chips_html = '<span class="label-chip active" data-label="__all__">All</span>'
        for lbl in all_labels:
            chips_html += f'<span class="label-chip" data-label="{lbl}">{lbl}</span>'
        label_bar_html = f"""
  <div class="label-bar">
    <span class="bar-title">Filter by label:</span>
    {chips_html}
  </div>"""

    # Build matrix
    fixture_ids = sorted({r.get("fixture_id") for r in uc_rows if not r.get("skipped")})
    eval_order  = [ev.id for ev in uc.evals]

    # Accumulate cell data: (fixture_id, eval_id) -> {passed, total, errors, scores}
    from collections import defaultdict
    cell: dict = defaultdict(lambda: {"passed": 0, "total": 0, "errors": 0, "scores": []})
    for r in uc_rows:
        if r.get("skipped"):
            continue
        key = (r["fixture_id"], r["eval_id"])
        if r.get("error"):
            cell[key]["errors"] += 1
        elif r.get("score") is not None:
            cell[key]["scores"].append(r["score"])
        else:
            cell[key]["total"] += 1
            if r.get("passed"):
                cell[key]["passed"] += 1

    # Build column headers
    tag_class = {"right": "right-col", "good": "good-col", "safe": "safe-col"}
    eval_meta = {ev.id: ev for ev in uc.evals}

    header_cells = '<th class="fixture-col">fixture</th>'
    for eid in eval_order:
        ev = eval_meta.get(eid)
        tc = tag_class.get(ev.tag if ev else "", "")
        labels_str = "|".join(ev.labels) if ev and ev.labels else ""
        header_cells += (
            f'<th class="{tc}" data-eval-id="{eid}" data-labels="{labels_str}">{eid}</th>'
        )

    # Build rows
    body_rows_html = ""
    for fid in fixture_ids:
        row_cells = f'<td class="fixture-cell">{fid}</td>'
        for eid in eval_order:
            d = cell[(fid, eid)]
            if d["scores"]:
                avg = round(sum(d["scores"]) / len(d["scores"]), 1)
                # Look up scale max from eval metadata for display
                ev = eval_meta.get(eid)
                scale_max = ev.scale[1] if ev and ev.scale else None
                label = f"{avg}/{scale_max}" if scale_max else f"{avg}"
                cls   = "cell-pass"  # scored cells are always clickable, neutral-positive
            elif d["errors"] > 0 and d["total"] == 0:
                cls   = "cell-error"
                label = "err"
            elif d["errors"] > 0:
                cls   = "cell-fail"
                label = f"{d['passed']}/{d['total']}+err"
            elif d["total"] == 0:
                cls   = "cell-skip"
                label = "—"
            else:
                all_pass = d["passed"] == d["total"]
                cls   = "cell-pass" if all_pass else "cell-fail"
                label = f"{d['passed']}/{d['total']}"
            row_cells += (
                f'<td class="{cls}" data-fixture-id="{fid}" data-eval-id="{eid}">'
                f'{label}</td>'
            )
        body_rows_html += f"<tr>{row_cells}</tr>\n"

    # Per-eval aggregate: a rate is uninterpretable without its interval and n.
    stats_by_eval: dict = {}
    for tag_stats in uc_summary.values():
        stats_by_eval.update(tag_stats)

    foot_cells = '<td class="fixture-col">all</td>'
    for eid in eval_order:
        stats = stats_by_eval.get(eid, {})
        fr    = stats.get("failure_rate")
        ci    = stats.get("failure_rate_ci")
        n     = stats.get("total_runs") or 0
        if fr is None:
            mean = stats.get("mean")
            label = f"{mean} (n={n})" if mean is not None else "—"
        else:
            pass_pct = round((1 - fr) * 100)
            if ci:
                # Pass-rate interval is the failure-rate interval inverted.
                low, high = round((1 - ci[1]) * 100), round((1 - ci[0]) * 100)
                label = f"{pass_pct}% [{low}–{high}%] n={n}"
            else:
                label = f"{pass_pct}% n={n}"
        foot_cells += f'<td class="agg-cell">{label}</td>'

    matrix_foot = f"<tfoot><tr>{foot_cells}</tr></tfoot>"

    matrix_html = f"""
  <div class="matrix-wrap">
    <table class="matrix">
      <thead><tr>{header_cells}</tr></thead>
      {matrix_foot}
      <tbody>
        {body_rows_html}
      </tbody>
    </table>
  </div>"""

    judge_tables_html = _build_judge_tables(uc_summary)

    return f"""
<div class="uc-section" data-uc="{uc_id}">
  <div class="uc-header">
    <h2>{uc_id}</h2>
    <p>{uc.description}</p>
  </div>
  {label_bar_html}
  {matrix_html}
  {judge_tables_html}
</div>"""


def _esc_py(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_judge_tables(uc_summary: dict) -> str:
    """
    Judge-vs-human agreement and judge repeatability, per eval.

    The markdown report has carried both since specs 07 and 06. The HTML did
    not, so the file `fieldtest view` opens said nothing about the judge that
    produced its numbers — while embedding every figure needed to say it.
    """
    labelled, repeats = [], []
    for tag_stats in uc_summary.values():
        for eval_id, st in tag_stats.items():
            if st.get("labeled_runs"):
                labelled.append((eval_id, st))
            if (st.get("judge_runs") or 1) > 1:
                repeats.append((eval_id, st))

    html = ""

    if labelled:
        rows = ""
        for eval_id, st in sorted(labelled):
            agreement = st.get("judge_agreement")
            pct = "—" if agreement is None else "%.1f%%" % (agreement * 100)
            cls = "judge-low" if (agreement is not None and agreement < 0.8) else ""
            rows += (
                "<tr><td>" + _esc_py(eval_id) + "</td>"
                "<td>" + str(st.get("labeled_runs", 0)) + "</td>"
                '<td class="' + cls + '">' + pct + "</td>"
                "<td>" + str(st.get("judge_false_pass", 0)) + " false pass, "
                + str(st.get("judge_false_fail", 0)) + " false fail</td></tr>"
            )
        html += (
            '\n  <div class="judge-block">\n'
            "    <h3>Judge vs your labels</h3>\n"
            '    <div class="matrix-wrap"><table class="matrix">\n'
            "      <thead><tr><th>eval</th><th>labelled runs</th>"
            "<th>agreement</th><th>errors</th></tr></thead>\n"
            "      <tbody>" + rows + "</tbody>\n"
            "    </table></div>\n"
            '    <p class="judge-note">A false pass is an output you failed and the judge '
            "passed. On a <strong>safe</strong> eval that is the error that matters.</p>\n"
            "  </div>"
        )

    if repeats:
        rows = ""
        for eval_id, st in sorted(repeats):
            dis = st.get("judge_disagreement_rate")
            sys_sd, jdg_sd = st.get("system_stddev"), st.get("judge_stddev")
            # A binary eval reports disagreement; a scored one reports the two
            # spreads. Dropping the spread columns — which this table did —
            # renders a scored eval as an empty row and loses the comparison
            # that judge_runs exists to make.
            rows += (
                "<tr><td>" + _esc_py(eval_id) + "</td>"
                "<td>" + str(st.get("judge_runs")) + "</td>"
                "<td>" + ("—" if dis is None else "%.1f%%" % (dis * 100)) + "</td>"
                "<td>" + ("—" if sys_sd is None else str(sys_sd)) + "</td>"
                "<td>" + ("—" if jdg_sd is None else str(jdg_sd)) + "</td></tr>"
            )
        html += (
            '\n  <div class="judge-block">\n'
            "    <h3>Judge repeatability</h3>\n"
            '    <div class="matrix-wrap"><table class="matrix">\n'
            "      <thead><tr><th>eval</th><th>judge runs</th>"
            "<th>judge disagreement</th><th>system spread</th>"
            "<th>judge spread</th></tr></thead>\n"
            "      <tbody>" + rows + "</tbody>\n"
            "    </table></div>\n"
            '    <p class="judge-note">Disagreement near zero means the judge is repeatable. '
            "For a scored eval, compare the two spreads: system spread is your outputs "
            "differing from each other, judge spread is the judge differing from itself on "
            "the same output. Judge spread approaching system spread means the criteria are "
            "ambiguous, not that the system is noisy.</p>\n"
            "  </div>"
        )

    return html


def _build_delta_html(delta: dict) -> str:
    """Build delta comparison section HTML. Returns empty string if no baseline."""
    baseline_id = delta.get("baseline_run_id")
    increased   = delta.get("increased", [])
    decreased   = delta.get("decreased", [])
    unchanged   = delta.get("unchanged", [])

    # The markdown carries three reasons not to trust a delta, and a reason a
    # baseline is missing. None of them reached the HTML, so this page showed
    # movement with none of the caveats attached to it — or, with no baseline,
    # showed nothing and left the reader to guess why.
    caveats = []
    if delta.get("baseline_error_share", 0) >= 0.1:
        caveats.append(
            f"The baseline lost {delta['baseline_error_share'] * 100:.0f}% of its "
            f"judge calls to errors, so its rates are over whatever survived. "
            f"These are not a like-for-like comparison."
        )
    if delta.get("baseline_pre_judge"):
        caveats.append(
            "The baseline predates judge tracking, so the judge that produced it "
            "is unknown and these deltas may reflect an instrument change."
        )
    changed = delta.get("sample_changed") or []
    if changed:
        caveats.append(
            f"{len(changed)} eval(s) scored a different number of outputs than the "
            f"baseline ({', '.join(changed[:4])}), so the deltas include a change "
            f"of population, not only a change in the system."
        )
    caveat_html = "".join(f'<p class="delta-caveat">⚠ {c}</p>' for c in caveats)

    if not baseline_id:
        reason = delta.get("no_baseline_reason")
        if not reason:
            return ""
        return f"""
<div class="delta-section">
  <h2>No baseline</h2>
  <p class="delta-caveat">{reason[0].upper() + reason[1:]}. Every eval reads
  &#8212; against prior for that reason, not because nothing moved.</p>
</div>"""

    rows_html = ""
    for item in increased:
        rows_html += (
            f'<tr><td>{item["eval_id"]}</td>'
            f'<td class="delta-up">+{round(item["delta"] * 100, 1)}%</td>'
            f'<td>{round(item.get("previous", 0) * 100, 1)}%</td>'
            f'<td>{round(item.get("current", 0) * 100, 1)}%</td></tr>'
        )
    for item in decreased:
        rows_html += (
            f'<tr><td>{item["eval_id"]}</td>'
            f'<td class="delta-down">{round(item["delta"] * 100, 1)}%</td>'
            f'<td>{round(item.get("previous", 0) * 100, 1)}%</td>'
            f'<td>{round(item.get("current", 0) * 100, 1)}%</td></tr>'
        )
    for eid in unchanged:
        rows_html += (
            f'<tr><td>{eid}</td><td class="no-change">↔</td><td>—</td><td>—</td></tr>'
        )

    if not rows_html:
        return ""

    return f"""
<div class="delta-section">
  <h2>Delta vs prior run ({baseline_id})</h2>
  {caveat_html}
  <table class="delta-table">
    <thead><tr><th>eval</th><th>change</th><th>before</th><th>after</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>"""
