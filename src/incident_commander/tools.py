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

These run behind one MCP server (mcp_server.py, FastMCP); the functions here are
the implementations it wraps. What the model reads about each tool (name, schema,
one-line description) lives in the server, not in these docstrings.
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
    series = [c for c in df.columns if c != "time"]
    services = sorted({c.rpartition("_")[0] for c in series})
    metrics = sorted({c.rpartition("_")[2] for c in series})

    def _match(want: str | None, have: str) -> bool:
        # tolerant: "latency" matches "latency-50"; "frontendservice" matches "frontend"
        if not want:
            return True
        w, h = want.lower(), have.lower()
        return w in h or h in w

    if service and not any(_match(service, s) for s in services):
        raise ValueError(f"unknown service {service!r}; metric services are {services}")
    if metric and not any(_match(metric, m) for m in metrics):
        raise ValueError(f"unknown metric {metric!r}; available metrics are {metrics}")

    out = []
    for col in series:
        svc, _, met = col.rpartition("_")
        if not (_match(service, svc) and _match(metric, met)):
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


def trace_lookup(case: IncidentCase, failing_only: bool = True, limit: int = 10,
                 service: str | None = None) -> dict:
    """Summarized request paths from the span table, never raw span dumps.

    Without `service`: picks the `limit` worst traces after the alert (errored
    first when failing_only, else slowest) and returns each as a service path
    with total duration and the error spans marked.
    With `service`: that service's own spans before vs after the alert (see
    _service_spans). This is the per-service trace signal CPU/mem faults need.
    """
    df = load_traces(case, columns=["traceID", "spanID", "serviceName",
                                    "operationName", "startTimeMillis",
                                    "duration", "statusCode", "parentSpanID"])
    if service:
        return _service_spans(case, df, service, limit)
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


def _service_spans(case: IncidentCase, df, service: str, limit: int) -> dict:
    """One service's spans, before vs after the alert, in code.

    Uses SELF time (span duration minus its direct children's durations, floored
    at 0), not raw duration: a caller's raw duration grows whenever anything
    downstream is slow, so raw duration would implicate every caller of the
    faulty service. Self time stays with the service doing the slow work.
    Also "inbound": self time of the CALLERS' client spans that target this
    service (op names like "hipstershop.CurrencyService/Convert"). Network delay
    or loss injected on a pod, or a server span that was never exported, shows
    up there and not in the service's own spans. Calibrated on rep 1 of RE2-OB:
    max(own, inbound) p50 ratio ranks the true service first in most cases.
    Returns overall/inbound/per-operation median self time and error rate.
    """
    names = sorted(df["serviceName"].dropna().unique())
    want = service.lower()
    match = [n for n in names if n.lower() == want] or [n for n in names if want in n.lower()]
    if len(match) != 1:
        raise ValueError(f"unknown service {service!r}; trace services are {names}")
    svc = match[0]
    child_us = df[df["parentSpanID"].notna()].groupby("parentSpanID")["duration"].sum()
    df = df.assign(self_us=(df["duration"] - df["spanID"].map(child_us).fillna(0)).clip(lower=0),
                   after=df["startTimeMillis"] >= case.inject_time * 1000,
                   err=df["statusCode"] != 0)
    own = df[df["serviceName"] == svc]
    target = svc.lower().removesuffix("service") + "service/"
    inbound = df[(df["serviceName"] != svc)
                 & df["operationName"].str.lower().str.contains(target, regex=False, na=False)]

    def side(g) -> dict:
        b, a = g[~g["after"]], g[g["after"]]
        p50b, p50a = b["self_us"].median() / 1000, a["self_us"].median() / 1000
        return {"n_before": int(len(b)), "n_after": int(len(a)),
                "self_p50_ms_before": round(p50b, 2), "self_p50_ms_after": round(p50a, 2),
                "self_p90_ms_before": round(b["self_us"].quantile(0.9) / 1000, 2),
                "self_p90_ms_after": round(a["self_us"].quantile(0.9) / 1000, 2),
                "p50_ratio": round(p50a / p50b, 2) if p50b > 0 else None,
                "err_rate_before": round(float(b["err"].mean()), 4) if len(b) else None,
                "err_rate_after": round(float(a["err"].mean()), 4) if len(a) else None}

    ops = [{"op": op, **side(g)} for op, g in own.groupby("operationName")]
    ops.sort(key=lambda o: -(o["p50_ratio"] or 0))
    return {"service": svc, "overall": side(own),
            "inbound": side(inbound) if len(inbound) else None, "operations": ops[:limit],
            "source_ref": f"traces.csv:serviceName={svc}"}


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
