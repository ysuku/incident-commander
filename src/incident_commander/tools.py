"""The five read-only diagnostic tools (Checkpoint 2.1 design).

Rules that apply to every tool:
- Read-only: tools observe the recorded incident, never change anything.
- Summaries, not dumps: each tool returns top matches / aggregates sized for a
  small model's context window, never raw telemetry.
- Citable: every returned item carries a source_ref (file + line / metric name
  + window) so the agent can cite it in the incident brief.
- Ground truth stays out: tools never read case.root_cause_service/fault_type.

Naming note: logs name services by container ("frontend"); traces use
"frontendservice". Tools return names as the underlying telemetry spells them.

These run behind one MCP server (FastMCP) from Module 2 onward; the functions
here are the implementations the server wraps.
"""
from .loaders import (IncidentCase, load_logs, load_simple_metrics,
                      load_traces)


def metric_query(case: IncidentCase, service: str | None = None,
                 metric: str | None = None, top: int = 10) -> dict:
    """Compare each metric series before vs. after the alert time, in code.

    Returns the `top` series by |z-score| of the after-mean against the before
    baseline: [{series, service, metric, before_mean, after_mean, pct_change, z}].
    Models are bad at arithmetic over long number series; this does it for them.
    """
    df = load_simple_metrics(case)
    before = df[df["time"] < case.inject_time]
    after = df[df["time"] >= case.inject_time]
    if before.empty or after.empty:
        raise ValueError(f"{case.case_id}: no data on one side of inject_time")
    out = []
    for col in df.columns:
        if col == "time":
            continue
        svc, _, met = col.rpartition("_")
        if service and svc != service:
            continue
        if metric and met != metric:
            continue
        b, a = before[col].astype(float), after[col].astype(float)
        b_mean, a_mean, b_std = b.mean(), a.mean(), b.std()
        # Floor the denominator at 5% of the baseline mean: a near-constant
        # series (std ~ 0) would otherwise turn a trivial shift into a huge z.
        denom = max(b_std, 0.05 * abs(b_mean), 1e-9)
        z = (a_mean - b_mean) / denom
        pct = ((a_mean - b_mean) / abs(b_mean) * 100) if abs(b_mean) > 1e-9 else float("inf")
        out.append({
            "series": col, "service": svc, "metric": met,
            "before_mean": round(b_mean, 4), "after_mean": round(a_mean, 4),
            "pct_change": round(pct, 1) if pct != float("inf") else None,
            "z": round(z, 2),
            "source_ref": f"simple_metrics.csv:{col}",
        })
    out.sort(key=lambda r: -abs(r["z"]))
    return {
        "inject_time": case.inject_time,
        "n_series_scanned": len(out),
        "top_deviations": out[:top],
    }


def log_search(case: IncidentCase, query: str = "", service: str | None = None,
               level: str | None = None, limit: int = 20) -> dict:
    """Search the case's logs; always returns counts plus at most `limit` lines.

    query: case-insensitive substring of the message ("" = match all — use with
    level="error" for a quick error sweep). service matches container_name.
    Lines nearest the alert time come first; each carries source_ref logs.csv#L<n>.
    """
    df = load_logs(case, columns=["timestamp", "container_name", "message", "level"])
    m = df
    if service:
        m = m[m["container_name"] == service]
    if level:
        m = m[m["level"].str.lower() == level.lower()]
    if query:
        m = m[m["message"].str.contains(query, case=False, na=False, regex=False)]
    counts = (m.groupby(["container_name", "level"]).size()
               .sort_values(ascending=False).head(15))
    inject_ns = case.inject_time * 1_000_000_000
    after = m[m["timestamp"] >= inject_ns].head(limit)
    sample = after if len(after) else m.tail(limit)
    return {
        "total_matches": int(len(m)),
        "matches_by_service_level": {f"{s}/{l}": int(n) for (s, l), n in counts.items()},
        "sample_after_alert": [{
            "ts": int(r.timestamp), "service": r.container_name,
            "level": r.level, "message": str(r.message)[:200],
            "source_ref": f"logs.csv#L{idx + 2}",
        } for idx, r in sample.iterrows()],
    }


def trace_lookup(case: IncidentCase, failing_only: bool = True, limit: int = 10) -> dict:
    """Summarized request paths from the span table, never raw span dumps.

    Picks the `limit` worst traces after the alert (errored first when
    failing_only, else slowest) and returns each as a service path with total
    duration and the error spans marked.
    """
    df = load_traces(case, columns=["traceID", "spanID", "serviceName",
                                    "operationName", "startTimeMillis",
                                    "duration", "statusCode", "parentSpanID"])
    df = df[df["startTimeMillis"] >= case.inject_time * 1000]
    if df.empty:
        return {"note": "no traces after alert time", "traces": []}
    g = df.groupby("traceID")
    stats = g.agg(total_us=("duration", "max"), n_spans=("spanID", "count"),
                  errors=("statusCode", lambda s: int((s != 0).sum())))
    pick = stats[stats["errors"] > 0] if failing_only and (stats["errors"] > 0).any() else stats
    pick = pick.sort_values(["errors", "total_us"], ascending=False).head(limit)
    traces = []
    for tid, row in pick.iterrows():
        spans = df[df["traceID"] == tid].sort_values("startTimeMillis")
        path, seen = [], set()
        for svc in spans["serviceName"]:
            if svc not in seen:
                seen.add(svc); path.append(svc)
        errs = spans[spans["statusCode"] != 0]
        traces.append({
            "trace_id": tid, "path": " -> ".join(path),
            "duration_ms": round(row["total_us"] / 1000, 1),
            "n_spans": int(row["n_spans"]),
            "error_spans": [{"service": e.serviceName, "op": e.operationName,
                             "status": float(e.statusCode)} for e in errs.itertuples()][:5],
            "source_ref": f"traces.csv:traceID={tid}",
        })
    return {"failing_only": failing_only and (stats["errors"] > 0).any(),
            "n_traces_after_alert": int(len(stats)), "traces": traces}


def service_map(case: IncidentCase) -> dict:
    """Who calls whom, built from parent->child span pairs across the traces.

    Returns {"edges": {caller: [callees...]}, "source_ref": "traces.csv"}.
    The real dependency graph grounds "what does checkout depend on" questions.
    """
    df = load_traces(case, columns=["spanID", "serviceName", "parentSpanID"])
    svc_of = dict(zip(df["spanID"], df["serviceName"]))
    edges: dict[str, set] = {}
    child = df[df["parentSpanID"].notna()]
    for parent_span, svc in zip(child["parentSpanID"], child["serviceName"]):
        caller = svc_of.get(parent_span)
        if caller and caller != svc:
            edges.setdefault(caller, set()).add(svc)
    return {"edges": {k: sorted(v) for k, v in sorted(edges.items())},
            "source_ref": "traces.csv (parent->child span pairs)"}


def runbook_lookup(symptom: str, k: int = 4) -> list[dict]:
    """Semantic search over public runbooks (Chroma + MiniLM, MMR, distance cut-off).

    `symptom` should be written from OBSERVED telemetry ("checkoutservice CPU at
    its limit, latency rising"), not copied from the raw alert.
    Returns up to k [{source_ref, alert, kind, distance, text}] or [] when nothing
    is close enough -- the agent must then say "no runbook match" rather than guess.
    Runbook hits are REFERENCE, not evidence: they may suggest candidates and known
    fixes, but a root-cause claim still needs two independent telemetry types.
    """
    from .retrieval import DEFAULT_COMPONENTS, RunbookIndex, load_chunks
    global _RUNBOOK_INDEX
    if _RUNBOOK_INDEX is None:
        _RUNBOOK_INDEX = RunbookIndex()
        if _RUNBOOK_INDEX.col.count() == 0:
            _RUNBOOK_INDEX.build(load_chunks())
    return _RUNBOOK_INDEX.search(symptom, k=k, components=DEFAULT_COMPONENTS)


_RUNBOOK_INDEX = None
