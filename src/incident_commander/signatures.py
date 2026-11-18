"""Fault signatures and the deterministic critic (Checkpoint 4.1, ToT evaluator).

A signature lists what each fault type must move in the telemetry. The critic
classifies every signal against the evidence gathered so far, in code:
  confirmed     measured, and moved as the hypothesis predicts
  contradicted  measured, and did NOT move (or moved the wrong way)
  inconclusive  measured, but in the grey zone between noise and a clear signal
  unobservable  the test was run but the series does not exist / is unusable
  untested      no evidence yet speaks to it (the generator should test it)
Only "contradicted" lowers a score. A branch is closed in two ways (tot.rescore):
a contradicted must-have signal, or a ceiling (score with every untested signal
counted as confirmed) that is PRUNE_GAP or more below the best live score, so a
branch can also be dropped for being out of reach. "No data" does neither. That
is the 4.1 mitigation for the data gaps: diskio exists for 4 of 11 services and
is unusable where it exists (z of 0, NaN or ~1e12+ in every fault type), and
there is no network metric, so "no data" must never be read as "no signal".
The other side of that rule: disk can never be contradicted, so "every branch
pruned" cannot happen. `unfamiliar` below is the reachable form of that test.

Calibrated on the RE2-OB dev set (90 cases) by looking at the true service's
signals per fault type; Train Ticket stays held out. Logs are deliberately NOT
in the table: the root-cause service logged zero error lines in all 90 cases, so
a logs signal would only ever "contradict" a true hypothesis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable

from .loop import ERR_RISE_SIGNIFICANT, Z_SIGNIFICANT, _norm

FAULT_TYPES = ("cpu", "mem", "disk", "socket", "delay", "loss")
STRONG_MEM = 15.0    # mem z: cpu faults reach 9, mem/disk faults 22+
STRONG_SOCKET = 25.0  # socket z: cpu/mem/disk faults reach 25, socket faults 28+
FLAT = 5.0           # "resources normal" for delay: delay faults never pass 3.4

CONFIRMED, CONTRADICTED, INCONCLUSIVE, UNOBSERVABLE, UNTESTED = (
    "confirmed", "contradicted", "inconclusive", "unobservable", "untested")


@dataclass
class Features:
    """What the evidence board says about one service, with the citing eids."""
    service: str
    z: dict[str, tuple[float, str]] = field(default_factory=dict)  # metric -> (z, eid)
    metrics_scoped: str | None = None   # eid of a metric_query(service=S): absent series = unobservable
    trace: dict | None = None            # trace_lookup(service=S) result
    trace_eid: str | None = None
    trace_implicates: bool = False       # code-computed (loop._summarize), caller-side logic included
    hidden: frozenset = frozenset()      # telemetry kinds withheld from this run (--hide): unobservable


@dataclass(frozen=True)
class Signal:
    name: str
    kind: str                      # metrics | traces
    expect: str                    # predicted result, plain language (goes in the brief)
    judge: Callable[[Features], tuple[str, str, str | None]]  # -> (verdict, observed, eid)
    must: bool = False             # contradicted must-have => prune the branch


def _metric(f: Features, metric: str) -> tuple[float | None, str | None, bool]:
    """(z, eid, tested) for one series; z None if absent or unusable."""
    if metric in f.z:
        z, eid = f.z[metric]
        return (z if math.isfinite(z) and abs(z) < 1e6 else None), eid, True
    # metrics hidden: the series cannot be read at all, which is "no data", not "not yet tested"
    return None, f.metrics_scoped, f.metrics_scoped is not None or "metrics" in f.hidden


def _threshold(metric: str, strong: float, label: str):
    """confirmed >= strong, contradicted < Z_SIGNIFICANT, grey zone inconclusive."""
    def judge(f: Features):
        z, eid, tested = _metric(f, metric)
        if not tested:
            return UNTESTED, "", None
        if z is None:
            return UNOBSERVABLE, f"no usable {metric} series", eid
        obs = f"{metric} z={z:g}"
        if z >= strong:
            return CONFIRMED, obs, eid
        return (CONTRADICTED if z < Z_SIGNIFICANT else INCONCLUSIVE), obs, eid
    return Signal(f"{label}", "metrics", f"{metric} z >= {strong:g}", judge)


def _below(metric: str, limit: float, label: str):
    """Confirmed when the series stays below `limit` (a rival fault's territory)."""
    def judge(f: Features):
        z, eid, tested = _metric(f, metric)
        if not tested:
            return UNTESTED, "", None
        if z is None:
            return UNOBSERVABLE, f"no usable {metric} series", eid
        return (CONFIRMED if z < limit else CONTRADICTED), f"{metric} z={z:g}", eid
    return Signal(label, "metrics", f"{metric} z < {limit:g}", judge)


def _latency_up(f: Features):
    vals = [(z, e) for m in ("latency-50", "latency-90")
            for z, e, t in [_metric(f, m)] if t]
    if not vals:
        return UNTESTED, "", None
    usable = [(z, e) for z, e in vals if z is not None]
    if not usable:
        return UNOBSERVABLE, "no latency series", vals[0][1]
    z, eid = max(usable)
    return (CONFIRMED if z >= Z_SIGNIFICANT else CONTRADICTED), f"latency z={z:g}", eid


def _resources(f: Features):
    """[(metric, z, eid)] for cpu/mem/socket that are present and usable; None if untested."""
    got = [(m, *_metric(f, m)) for m in ("cpu", "mem", "socket")]
    if not any(t for _, _, _, t in got):
        return None
    return [(m, z, e) for m, z, e, _ in got if z is not None]


def _resources_flat(f: Features):
    res = _resources(f)
    if res is None:
        return UNTESTED, "", None
    if not res:
        return UNOBSERVABLE, "no resource series", f.metrics_scoped
    m, z, eid = max(res, key=lambda r: r[1])
    return (CONFIRMED if z < FLAT else CONTRADICTED), f"max resource {m} z={z:g}", eid


def _conn_pressure(f: Features):
    """Loss: retransmits hold buffers and sockets, so mem or socket rises with no cpu
    rise. Delay leaves both flat. This is what separates loss from delay without a
    network metric."""
    res = _resources(f)
    if res is None:
        return UNTESTED, "", None
    held = [(m, z, e) for m, z, e in res if m in ("mem", "socket")]
    if not held:
        return UNOBSERVABLE, "no mem/socket series", f.metrics_scoped
    m, z, eid = max(held, key=lambda r: r[1])
    return (CONFIRMED if z >= FLAT else CONTRADICTED), f"{m} z={z:g}", eid


def _no_trace(f: Features) -> tuple[str, str, None]:
    """No trace result on the board: untested, unless traces are hidden in this
    run. Then no test can ever speak to the signal, so it is unobservable."""
    if "traces" in f.hidden:
        return UNOBSERVABLE, "traces hidden in this run", None
    return UNTESTED, "", None


def _trace_side(f: Features):
    if f.trace is None:
        return None
    sides = [s for s in (f.trace.get("overall"), f.trace.get("inbound")) if s and s.get("n_after")]
    rise = max(((s.get("err_rate_after") or 0) - (s.get("err_rate_before") or 0) for s in sides),
               default=0.0)
    return sides, rise


def _trace_slow(contradicts: bool):
    """Traces implicate S (code-computed, incl. the caller-side rule). For delay,
    flat traces contradict; for resource faults they are only supporting, because
    a cpu fault on checkout leaves its spans flat (8 of 15 cpu cases)."""
    def judge(f: Features):
        if f.trace is None:
            return _no_trace(f)
        if f.trace_implicates:
            return CONFIRMED, "traces implicate the service", f.trace_eid
        return (CONTRADICTED if contradicts else INCONCLUSIVE), "traces flat", f.trace_eid
    return Signal("trace_slow", "traces", "service's own/inbound spans slow down", judge)


def _errors(want_up: bool):
    def judge(f: Features):
        side = _trace_side(f)
        if side is None:
            return _no_trace(f)
        sides, rise = side
        if not sides:
            return UNOBSERVABLE, "no spans after alert", f.trace_eid
        up = rise >= ERR_RISE_SIGNIFICANT
        obs = f"span error rate +{rise:.3f}"
        if want_up:  # loss at a low rate shows retries, not errors: absence is not proof
            return (CONFIRMED if up else INCONCLUSIVE), obs, f.trace_eid
        return (CONTRADICTED if up else CONFIRMED), obs, f.trace_eid
    name, expect = (("errors_up", f"span error rate rises >= {ERR_RISE_SIGNIFICANT:g}") if want_up
                    else ("no_errors", f"span error rate rises < {ERR_RISE_SIGNIFICANT:g}"))
    return Signal(name, "traces", expect, judge)


def _diskio(f: Features):
    _, eid, tested = _metric(f, "diskio")
    if not tested:
        return UNTESTED, "", None
    return UNOBSERVABLE, "diskio missing or unusable (z 0/NaN/1e12+ in every fault type)", eid


SIGNATURES: dict[str, list[Signal]] = {
    "cpu": [
        Signal("cpu_up", "metrics", f"cpu z >= {Z_SIGNIFICANT:g}",
               _threshold("cpu", Z_SIGNIFICANT, "cpu_up").judge, must=True),
        _below("mem", STRONG_MEM, "mem_not_saturated"),
        _below("socket", STRONG_SOCKET, "socket_not_saturated"),
        _trace_slow(contradicts=False),
    ],
    "mem": [
        Signal("mem_up", "metrics", f"mem z >= {STRONG_MEM:g}",
               _threshold("mem", STRONG_MEM, "mem_up").judge, must=True),
        Signal("latency_up", "metrics", "latency z >= 3", _latency_up),
        _below("socket", STRONG_SOCKET, "socket_not_saturated"),
        _trace_slow(contradicts=False),
    ],
    "disk": [
        Signal("diskio_up", "metrics", "diskio rises", _diskio, must=True),
        _threshold("mem", STRONG_MEM, "mem_up"),  # page cache fills; data shows mem z 25+
        _threshold("cpu", Z_SIGNIFICANT, "cpu_up"),
        _below("socket", STRONG_SOCKET, "socket_not_saturated"),
        _trace_slow(contradicts=False),
    ],
    "socket": [
        Signal("socket_up", "metrics", f"socket z >= {STRONG_SOCKET:g}",
               _threshold("socket", STRONG_SOCKET, "socket_up").judge, must=True),
        _trace_slow(contradicts=False),
    ],
    "delay": [
        Signal("latency_up", "metrics", "latency z >= 3", _latency_up, must=True),
        Signal("resources_flat", "metrics", f"cpu, mem, socket all z < {FLAT:g}",
               _resources_flat, must=True),
        _trace_slow(contradicts=True),
        _errors(want_up=False),
    ],
    "loss": [
        Signal("latency_up", "metrics", "latency z >= 3", _latency_up, must=True),
        Signal("conn_pressure", "metrics", f"mem or socket z >= {FLAT:g}", _conn_pressure),
        _below("cpu", Z_SIGNIFICANT, "cpu_not_up"),
        _trace_slow(contradicts=False),
        _errors(want_up=True),
    ],
}

# One-line candidate fixes; every fix still waits for human approval (loop._gate).
REMEDIATION = {
    "cpu": "Raise the CPU limit (keep the request) or scale out {svc}, after confirming the app is not looping",
    "mem": "Raise the memory limit for {svc} and check for a leak before restarting pods",
    "disk": "Find the process filling disk I/O on {svc}; move scratch writes off the container disk",
    "socket": "Check {svc} for leaked connections; raise the socket/file-descriptor limit only after",
    "delay": "Inspect {svc}'s pod network (tc/netem rules, CNI); remove the injected latency",
    "loss": "Inspect {svc}'s pod network for packet loss (tc/netem, NIC errors); fix before retrying",
}


def features(service: str, observations: dict, implicates: dict,
             hidden: frozenset = frozenset()) -> Features:
    """Collect one service's features from raw tool results.

    observations: eid -> (tool, args, result); implicates: eid -> services the
    evidence implicates (computed by loop._summarize, so the critic and the
    citation gate agree on what "implicates" means). hidden: telemetry kinds
    withheld from the run; their signals are judged unobservable.
    """
    f = Features(service, hidden=frozenset(hidden))
    want = _norm(service)
    for eid, (name, args, res) in observations.items():
        if name == "metric_query":
            for d in res.get("top_deviations", []):
                if _norm(d["service"]) == want:
                    f.z.setdefault(d["metric"], (float(d["z"]), eid))
            if _norm(args.get("service")) == want and not args.get("metric") and f.metrics_scoped is None:
                f.metrics_scoped = eid
        elif name == "trace_lookup" and _norm(res.get("service")) == want and f.trace is None:
            f.trace, f.trace_eid = res, eid
            f.trace_implicates = want in {_norm(s) for s in implicates.get(eid, [])}
    return f


@dataclass
class Verdict:
    signal: str
    kind: str
    verdict: str
    observed: str
    eid: str | None
    must: bool
    expect: str


def judge(fault: str, f: Features) -> list[Verdict]:
    """The critic: classify every signal of one hypothesis. Pure function."""
    out = []
    for s in SIGNATURES[fault]:
        v, obs, eid = s.judge(f)
        out.append(Verdict(s.name, s.kind, v, obs, eid, s.must, s.expect))
    return out


def score(verdicts: list[Verdict]) -> int:
    """+1 per confirmed signal, +1 once if confirmed signals span two telemetry
    kinds, -2 per contradicted. (4.1 weights; to be tuned on the dev set.)"""
    confirmed = [v for v in verdicts if v.verdict == CONFIRMED]
    kinds = {v.kind for v in confirmed}
    return (len(confirmed) + (1 if len(kinds) >= 2 else 0)
            - 2 * sum(v.verdict == CONTRADICTED for v in verdicts))


def ceiling(verdicts: list[Verdict]) -> int:
    """Best score the branch could still reach: every untested signal confirmed.
    A branch is only pruned or out-ranked on this, never on its current score, so
    a branch that has simply been tested less is not beaten by one tested more."""
    return score([replace(v, verdict=CONFIRMED) if v.verdict == UNTESTED else v for v in verdicts])


def must_contradicted(verdicts: list[Verdict]) -> list[Verdict]:
    return [v for v in verdicts if v.must and v.verdict == CONTRADICTED]


def rank_faults(f: Features) -> list[tuple[str, int, list[Verdict]]]:
    """All six fault types for one service, best first; must-contradicted last."""
    rows = [(fault, score(vs), vs) for fault in FAULT_TYPES for vs in [judge(fault, f)]]
    return sorted(rows, key=lambda r: (not must_contradicted(r[2]), r[1]), reverse=True)


def unfamiliar(f: Features) -> bool:
    """No known fault type fits this service: every type whose must-have signals
    can be observed is contradicted, and whatever survives does so only because
    its must-have signals are unobservable (today: disk, which has no usable
    diskio series) while scoring 0 or less. An untested or inconclusive must-have
    is NOT unfamiliar: the service has simply not been tested enough. Nor is a
    run with metrics hidden: every must-have is a metric, so nothing was ruled out.
    Nor is a service with no usable cpu/mem/socket series (frontend-external): its
    resource types survive only for want of data, and nothing ruled them out.
    This says only that no type FITS. Whether the service is the affected one at
    all is the caller's question (tot.ruled_out_types): a wrong suspect is flat too."""
    if "metrics" in f.hidden or not _resources(f):
        return False
    alive = [(s, vs) for _, s, vs in rank_faults(f) if not must_contradicted(vs)]
    return all(s <= 0 and all(v.verdict == UNOBSERVABLE for v in vs if v.must) for s, vs in alive)
