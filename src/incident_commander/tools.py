"""The five read-only diagnostic tools (Checkpoint 2.1 design; input checks 6.1).

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
import csv
import functools
import math
import sys

from .loaders import (NO_LEVEL, IncidentCase, load_logs, load_simple_metrics,
                      load_traces, span_callees)

MIN_FRAGMENT = 3  # shorter service/metric fragments match almost everything ('e', 'io')
LOG_LEVELS = ("debug", "info", "warn", "warning", "error", "fatal", "critical")
LEVEL_SYNONYMS = ({"warn", "warning"}, {"error", "err"})  # one level, two spellings


class Refusal(ValueError):
    """A tool declining a call the model can correct (unknown name, bad argument).
    Anything else a tool raises is a crash on the data: the MCP server marks it
    so the run records an error instead of treating it as the model's mistake."""


def _is_error(status):
    """Which spans failed, from a statusCode column that mixes gRPC codes (0 = OK,
    1-16 = error) and HTTP codes (2xx/3xx = OK, 4xx/5xx = error). A missing status
    was not recorded: it is not an error."""
    return status.notna() & (status != 0) & ~status.between(100, 399)


@functools.lru_cache(maxsize=4)
def _record_lines(path: str) -> tuple[int, ...]:
    """The physical line (1-based) each CSV record of a log file starts on. A quoted
    field in any column can span lines (the RE2 `error` column, RE3 messages) and
    pandas skips blank lines, so neither the row number nor one column's newlines
    give it. One pass over the file, kept for the few calls a case makes."""
    limit = csv.field_size_limit(sys.maxsize)  # one stack trace can pass the 128 KB default
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            next(reader, None)                 # header
            starts, prev = [], reader.line_num
            for row in reader:
                if row:                        # pandas drops blank lines
                    starts.append(prev + 1)
                prev = reader.line_num
            return tuple(starts)
    finally:
        csv.field_size_limit(limit)


def _bare(name) -> str:
    """'frontendservice' and 'frontend' are one service: logs and traces spell it differently."""
    s = str(name or "").lower().strip()
    return s[: -len("service")] if s.endswith("service") else s


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

    service, metric = (service or "").strip(), (metric or "").strip()
    top = max(1, top)  # top=0 would read as "no series in scope", which is false

    def _match(want: str, have: str) -> bool:
        # tolerant: "latency" matches "latency-50"; "frontendservice" matches "frontend"
        if not want:
            return True
        w, h = want.lower(), have.lower()
        return w in h or h in w

    def _known(want: str, names: list[str]) -> bool:
        # a 1-2 letter fragment is a substring of nearly every name: not a filter
        if len(want) < MIN_FRAGMENT and want.lower() not in (n.lower() for n in names):
            return False
        return any(_match(want, n) for n in names)

    if service and not _known(service, services):
        raise Refusal(f"unknown service {service!r}; metric services are {services}")
    if metric and not _known(metric, metrics):
        raise Refusal(f"unknown metric {metric!r}; available metrics are {metrics}")

    out = []
    for col in series:
        svc, _, met = col.rpartition("_")
        if not (_match(service, svc) and _match(metric, met)):
            continue
        b, a = before[col].astype(float), after[col].astype(float)
        b_mean, a_mean, b_std = b.mean(), a.mean(), b.std()
        # No sample before the alert and samples after it: the series starts with
        # the incident. That is the flat-at-0 case below with an empty cell where
        # the 0 would be, so read the baseline as 0 instead of leaving z = NaN.
        baseline = "recorded"
        if b.count() == 0 and a.count() > 0:
            b_mean, baseline = 0.0, "absent"
        if math.isnan(b_std):  # zero or one baseline sample has no spread to measure
            b_std = 0.0
        # Floor the denominator at 5% of the baseline mean: a near-constant
        # series (std ~ 0) would otherwise turn a trivial shift into a huge z.
        denom = max(b_std, 0.05 * abs(b_mean), 1e-9)
        if denom <= 1e-9:
            # Baseline flat at 0 (an error or disk series that only starts with the
            # incident): it has no scale of its own, and 1e-9 would make z about
            # after_mean * 1e9, above every real deviation. Use 5% of the post-alert
            # mean instead: such a series reads z = +/-20, significant but bounded.
            denom = max(0.05 * abs(a_mean), 1e-9)
            baseline = "absent" if baseline == "absent" else "zero"
        z = (a_mean - b_mean) / denom
        pct = ((a_mean - b_mean) / abs(b_mean) * 100) if abs(b_mean) > 1e-9 else float("inf")
        out.append({
            "series": col, "service": svc, "metric": met,
            "before_mean": round(b_mean, 4), "after_mean": round(a_mean, 4),
            "pct_change": round(pct, 1) if pct != float("inf") else None,
            "z": round(z, 2),
            # "zero"/"absent": z is the bounded +/-20, a flag that the series moved off
            # nothing, not a measured size. The fault signatures do not read it as one.
            "baseline": baseline,
            "source_ref": f"simple_metrics.csv:{col}",
        })
    # a series with no data on one side has z = NaN: rank it last, or the sort is no ranking
    out.sort(key=lambda r: -abs(r["z"]) if math.isfinite(r["z"]) else float("inf"))
    return {
        "inject_time": case.inject_time,
        "n_series_scanned": len(out),
        "top_deviations": out[:top],
    }


def log_search(case: IncidentCase, query: str = "", service: str | None = None,
               level: str | None = None, limit: int = 20) -> dict:
    """Search the case's logs; always returns counts plus at most `limit` lines.

    query: case-insensitive LITERAL substring of the message ("" = match all — use
    with level="error" for a quick error sweep). service matches container_name
    ("frontendservice" finds "frontend"). Lines nearest the alert time come first;
    each carries source_ref logs.csv#L<n>.
    A name this case's logs do not have is an error listing the ones they do: a
    silent zero would read as "this service logged nothing wrong". The error says
    which of two things it is: a real service with no log lines here (it has
    metrics or spans), or a name no telemetry knows. A valid level with no lines
    is a real zero; scope_lines and levels_present say what was searched. When
    the logs record no severity (severity_recorded False, RE3), a level filter
    finds nothing and that is NOT a zero: the caller must word it so.
    after_alert_by_service_level and window_seconds let the caller tell a rise
    after the alert from lines a service writes all the time.
    """
    query, limit = query or "", max(1, limit)
    if "|" in query:
        raise Refusal(f"query {query!r} is a literal substring, not a regex: "
                         "'|' is matched as a character; search one term per call")
    df = load_logs(case, columns=["timestamp", "container_name", "message", "level"])
    # a quoted message may span several physical lines (stack traces): the cited
    # line is where the row starts in the file, not its row number
    try:
        starts = _record_lines(str(case.path("logs")))
    except (OSError, csv.Error):
        starts = ()
    if len(starts) != len(df):  # unreadable, or the two parsers split it differently: estimate
        extra = df["message"].fillna("").astype(str).str.count("\n")
        starts = df.index + 2 + (extra.cumsum() - extra)
    line_of = dict(zip(df.index, starts))
    inject_ns = case.inject_time * 1_000_000_000
    window = {"before": max(0.0, (inject_ns - df["timestamp"].min()) / 1e9) if len(df) else 0.0,
              "after": max(0.0, (df["timestamp"].max() - inject_ns) / 1e9) if len(df) else 0.0}
    m = df
    if service:
        names = sorted(str(n) for n in df["container_name"].dropna().unique())
        match = [n for n in names if n == service] or [n for n in names if _bare(n) == _bare(service)]
        if not match:
            if _known_elsewhere(case, service):  # e.g. currencyservice: answers in metrics and traces
                raise Refusal(f"no log lines from {service!r} in this case (it is a known service: it "
                              f"may not log, or it went silent); services with logs in this case are {names}")
            raise Refusal(f"unknown service {service!r}; services with logs in this case are {names}")
        m = m[m["container_name"] == match[0]]
    lower = m["level"].astype("string").str.lower()  # string: a numeric or all-empty column still has .str
    present = sorted({str(l) for l in lower.dropna().unique() if str(l).strip()})
    scope_lines = int(len(m))
    # lines in the searched scope with no usable level (empty, blank or "unknown")
    unlevelled = int(lower.fillna("").str.strip().isin(["", NO_LEVEL]).sum())
    # False: no line in this scope records a severity (the whole file, or just this service)
    severity = not (scope_lines and unlevelled == scope_lines)
    if level:
        want = next((set(g) for g in LEVEL_SYNONYMS if level.lower() in g), {level.lower()})
        valid = {*LOG_LEVELS, *present}
        if not want & valid:
            raise Refusal(f"unknown level {level!r}; valid levels are {sorted(valid - {NO_LEVEL})}"
                          + ("" if severity else "; these logs record no severity, so search the "
                                                   "message text with query= instead"))
        m = m[lower.isin(want)]
    if query:
        m = m[m["message"].str.contains(query, case=False, na=False, regex=False)]
    # No line in the whole file is dated at or after the alert: the timestamps are
    # not in nanoseconds, or the logs stop before it. Every line would count as
    # "before", so there is no split to give and the caller keeps the plain reading.
    split = bool((df["timestamp"] >= inject_ns).any())
    # counted per lower-cased level: "WARN" and "warn" are one level
    m = m.assign(level_lc=lower.reindex(m.index))  # reindex: an empty frame would take the Series' rows
    is_after = m["timestamp"] >= inject_ns
    counts = (m.groupby(["container_name", "level_lc"]).size()
               .sort_values(ascending=False).head(15))
    counts_after = m[is_after].groupby(["container_name", "level_lc"]).size()
    after = m[is_after].head(limit)
    sample = after if len(after) else m.tail(limit)
    return {
        "total_matches": int(len(m)),
        "scope_lines": scope_lines,        # lines for the service (or the case) before level/query
        "levels_present": present,
        "severity_recorded": severity,
        "unlevelled_lines": unlevelled,    # in-scope lines a level filter cannot see
        "matches_by_service_level": {f"{s}/{l}": int(n) for (s, l), n in counts.items()},
        # the same keys, lines at or after the alert only: a rise is a symptom, routine noise is not
        "after_alert_by_service_level": ({f"{s}/{l}": int(counts_after.get((s, l), 0)) for (s, l) in counts.index}
                                         if split else None),
        "window_seconds": {k: round(v, 1) for k, v in window.items()},
        "sample_from": "after_alert" if len(after) else "before_alert" if split else "no_split",
        "sample_after_alert": [{
            "ts": int(r.timestamp), "service": r.container_name,
            "level": r.level, "message": str(r.message)[:200],
            "source_ref": f"logs.csv#L{int(line_of[idx])}",
        } for idx, r in sample.iterrows()],
    }


def _known_elsewhere(case: IncidentCase, service: str) -> bool:
    """Is `service` a name the metrics or the traces of this case have? Only read
    on the error path of log_search, to word the refusal truthfully."""
    want = _bare(service)
    try:
        cols = load_simple_metrics(case).columns
        if want in {_bare(c.rpartition("_")[0]) for c in cols if c != "time"}:
            return True
        return want in {_bare(n) for n in load_traces(case, columns=["serviceName"])["serviceName"].dropna().unique()}
    except (OSError, ValueError):  # no such file: nothing vouches for the name
        return False


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
    if df["statusCode"].dtype.kind not in "biuf":
        # status recorded as text ("OK"/"ERROR", or text mixed with codes): keep the
        # codes that parse; a name is not a code, and _is_error reads NaN as not recorded
        import pandas as pd
        df["statusCode"] = pd.to_numeric(df["statusCode"], errors="coerce")
    limit = max(1, limit)  # limit=0 would read as "no traces", which is false
    if service:
        return _service_spans(case, df, service, limit)
    df = df[df["startTimeMillis"] >= case.inject_time * 1000]
    if df.empty:
        return {"note": "no traces after alert time", "traces": []}
    df = df.assign(is_err=_is_error(df["statusCode"]))
    g = df.groupby("traceID")
    stats = g.agg(total_us=("duration", "max"), n_spans=("spanID", "count"), errors=("is_err", "sum"))
    pick = stats[stats["errors"] > 0] if failing_only and (stats["errors"] > 0).any() else stats
    pick = pick.sort_values(["errors", "total_us"], ascending=False).head(limit)
    traces = []
    for tid, row in pick.iterrows():
        spans = df[df["traceID"] == tid].sort_values("startTimeMillis")
        path, seen = [], set()
        for svc in spans["serviceName"]:
            if svc not in seen:
                seen.add(svc); path.append(svc)
        errs = spans[spans["is_err"]]
        traces.append({
            "trace_id": tid, "path": " -> ".join(path),
            "duration_ms": round(row["total_us"] / 1000, 1),
            "n_spans": int(row["n_spans"]),
            "error_spans": [{"service": e.serviceName, "op": e.operationName,
                             "status": float(e.statusCode)} for e in errs.itertuples()][:5],
            "source_ref": f"traces.csv:traceID={tid}",
        })
    return {"failing_only": bool(failing_only and (stats["errors"] > 0).any()),
            "n_traces_after_alert": int(len(stats)), "traces": traces}


def _service_spans(case: IncidentCase, df, service: str, limit: int) -> dict:
    """One service's spans, before vs after the alert, in code.

    Uses SELF time (span duration minus its direct children's durations, floored
    at 0), not raw duration: a caller's raw duration grows whenever anything
    downstream is slow, so raw duration would implicate every caller of the
    faulty service. Self time stays with the service doing the slow work.
    Also "inbound": self time of the CALLERS' client spans that target this
    service (loaders.span_callees: the op name on Online Boutique, e.g.
    "hipstershop.CurrencyService/Convert"; the span graph elsewhere). Network delay
    or loss injected on a pod, or a server span that was never exported, shows
    up there and not in the service's own spans. Calibrated on rep 1 of RE2-OB:
    max(own, inbound) p50 ratio ranks the true service first in most cases.
    Inbound is also split by caller, with each caller's calls to its OTHER
    callees: delay injected on a caller's network slows every callee it calls
    (checkoutservice_delay made email, shipping and currency all look 50x slower
    from checkout's side, flat from frontend's). "outbound" is the same view of
    this service's own calls, by callee.
    Returns overall/inbound/per-operation median self time and error rate.
    """
    child_us = df[df["parentSpanID"].notna()].groupby("parentSpanID")["duration"].sum()
    # which service each span calls; not tied to Online Boutique's operation names,
    # so the inbound/outbound views (and the gate's shared-cause rule) work elsewhere
    callee = span_callees(df).astype(object)  # object: an all-NaN column still has .str
    df = df.assign(self_us=(df["duration"] - df["spanID"].map(child_us).fillna(0)).clip(lower=0),
                   after=df["startTimeMillis"] >= case.inject_time * 1000,
                   err=_is_error(df["statusCode"]), callee=callee)
    calls = df[df["callee"].notna() & (df["callee"] != df["serviceName"])]
    # some services (shipping, cart, ad) export no spans of their own; they still
    # appear as callees, so their inbound view is all there is
    names = sorted(set(df["serviceName"].dropna()) | set(calls["callee"]))
    want = service.lower()
    match = [n for n in names if n.lower() == want] or [n for n in names if want in n.lower()]
    if len(match) != 1:
        raise Refusal(f"unknown service {service!r}; trace services are {names}")
    svc = match[0]
    own = df[df["serviceName"] == svc]
    # callees are lower-case on Online Boutique (from the op name) and spelled as
    # serviceName elsewhere: compare both ways without case
    callee_lc = calls["callee"].str.lower()
    inbound = calls[callee_lc == svc.lower()]

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

    def by_callee(caller: str, skip: str = "") -> list[dict]:
        g = calls[(calls["serviceName"] == caller) & (callee_lc != skip)]
        return [{"callee": c, "p50_ratio": side(h)["p50_ratio"]} for c, h in g.groupby("callee")]

    ops = [{"op": op, **side(g)} for op, g in own.groupby("operationName")]
    ops.sort(key=lambda o: -(o["p50_ratio"] or 0))
    by_caller = [{"caller": c, **side(g), "caller_other_callees": by_callee(c, skip=svc.lower())}
                 for c, g in inbound.groupby("serviceName")]
    return {"service": svc, "overall": side(own),
            "inbound": side(inbound) if len(inbound) else None,
            "inbound_by_caller": by_caller, "outbound": by_callee(svc),
            "operations": ops[:limit], "source_ref": f"traces.csv:serviceName={svc}"}


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
