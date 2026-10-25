"""Short-term memory: the evidence scratchpad (Checkpoint 2.1).

Lives for one investigation. Holds the alert, every tool result in summarized,
cited form, and the candidate list the model reported last. Each loop iteration
sees ONLY this compact rendering — never raw telemetry.

Evidence kinds: metrics / logs / traces count as independent telemetry; map and
runbook are reference context and do not satisfy the two-kinds stop rule.
"""
from dataclasses import dataclass, field

TELEMETRY_KINDS = {"metrics", "logs", "traces"}


@dataclass
class Evidence:
    eid: str            # citation id: E1, E2, ...
    kind: str           # metrics | logs | traces | map | runbook
    summary: str        # compact, model-facing
    source_ref: str     # file/line/series the claim traces to
    implicates: list[str] = field(default_factory=list)  # services this evidence points at (set by code)


@dataclass
class Scratchpad:
    alert: str
    evidence: list[Evidence] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)  # model's last ranking, free text
    notes: list[str] = field(default_factory=list)       # contradictions, loop warnings

    def add(self, kind: str, summary: str, source_ref: str,
            implicates: list[str] | None = None) -> Evidence:
        ev = Evidence(f"E{len(self.evidence) + 1}", kind, summary, source_ref,
                      list(implicates or []))
        self.evidence.append(ev)
        return ev

    def get(self, eid: str) -> Evidence | None:
        return next((e for e in self.evidence if e.eid == eid), None)

    def telemetry_kinds(self, eids: list[str]) -> set[str]:
        """Distinct independent telemetry kinds among the cited evidence ids."""
        return {e.kind for i in eids if (e := self.get(i))} & TELEMETRY_KINDS

    def render(self) -> str:
        lines = [f"ALERT: {self.alert}", "", "EVIDENCE SO FAR:"]
        if not self.evidence:
            lines.append("  (none yet)")
        for e in self.evidence:
            lines.append(f"  {e.eid} [{e.kind}] {e.summary} (source: {e.source_ref})")
        if self.candidates:
            lines += ["", "YOUR CURRENT CANDIDATES (your own last ranking):"]
            lines += [f"  {i+1}. {c}" for i, c in enumerate(self.candidates)]
        if self.notes:
            lines += ["", "NOTES:"] + [f"  - {n}" for n in self.notes]
        return "\n".join(lines)
