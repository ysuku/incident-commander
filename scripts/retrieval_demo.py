"""Checkpoint 3.1 demo: build the runbook index and show what retrieval changes.

Run from the capstone/ folder (needs network once, to fetch the MiniLM model):
    python scripts/retrieval_demo.py | tee evals/retrieval_demo_output.txt

Part 1  Worked example: the checkoutservice_cpu case, query written the way the
        agent would write it AFTER seeing metrics (symptoms, not the answer).
Part 2  Tiny dev check (6 hand-written symptom queries, one per RCAEval fault
        family): does a sensible runbook land in the top K under three setups?
        This is a smoke test of design choices, not an evaluation result.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from incident_commander import retrieval as R  # noqa: E402

# (fault family, symptom query as the agent would phrase it, acceptable runbooks or None = should abstain)
DEV = [
    ("cpu", "service container CPU usage pinned at its limit, request latency rising, timeouts upstream",
     {"CPUThrottlingHigh", "KubeCPUOvercommit", "KubeCPUQuotaOvercommit"}),
    ("mem", "service memory usage climbing to its limit, container restarts, out of memory kills",
     {"KubeMemoryOvercommit", "KubeMemoryQuotaOvercommit", "KubePodCrashLooping"}),
    ("disk", "disk space on the node filling up, writes failing, filesystem almost full",
     {"NodeFilesystemSpaceFillingUp", "NodeFilesystemAlmostOutOfSpace", "KubePersistentVolumeFillingUp",
      "NodeFilesystemFilesFillingUp", "NodeFilesystemAlmostOutOfFiles"}),
    ("socket", "service running out of open sockets and file descriptors, connection errors, conntrack table full",
     {"NodeFileDescriptorLimit", "NodeHighNumberConntrackEntriesUsed"}),
    ("loss", "packet loss and network receive errors between pods on a node, retransmissions",
     {"NodeNetworkReceiveErrs", "NodeNetworkTransmitErrs"}),
    ("delay", "added network latency between two services, p99 latency up, no errors, CPU and memory normal",
     None),  # no runbook covers pure injected latency -> ideal behaviour is to abstain
]


def show(hits, indent="   "):
    if not hits:
        print(f"{indent}(no chunk within distance threshold -> agent must say 'no runbook match')")
    for h in hits:
        print(f"{indent}{h['distance']:.3f}  {h['source_ref']}")


def main():
    idx = R.RunbookIndex(persist_dir=None, collection="runbooks_demo")      # real MiniLM embedder
    in_scope = R.load_chunks()
    wide = R.load_chunks(components=("kubernetes", "node", "prometheus", "prometheus-operator",
                                     "alertmanager", "etcd", "general", "kube-state-metrics"))
    n = idx.build(wide)
    print(f"Indexed {n} chunks from {len({c.alert for c in wide})} runbooks "
          f"({len(in_scope)} chunks / {len({c.alert for c in in_scope})} runbooks in default scope)\n")

    print("=== Part 1: worked example (checkoutservice_cpu) ===")
    q = DEV[0][1]
    print(f"query: {q}\n")
    print(" A. plain top-4, whole corpus, no threshold:")
    show(idx.search(q, max_distance=None, use_mmr=False))
    print(" B. scoped to kubernetes+node:")
    show(idx.search(q, max_distance=None, use_mmr=False, components=R.DEFAULT_COMPONENTS))
    print(f" C. scoped + threshold {R.MAX_DISTANCE} + MMR(lambda={R.MMR_LAMBDA}):  <- what runbook_lookup returns")
    hits = idx.search(q, components=R.DEFAULT_COMPONENTS)
    show(hits)
    if hits:
        print("\n top hit text (what the model actually receives):\n")
        print("   " + hits[0]["text"][:700].replace("\n", "\n   "))

    print("\n=== Part 2: tiny dev check (6 queries) ===")
    setups = {
        "A whole corpus, top-4":         dict(max_distance=None, use_mmr=False),
        "B scoped, top-4":               dict(max_distance=None, use_mmr=False, components=R.DEFAULT_COMPONENTS),
        "C scoped + threshold + MMR":    dict(components=R.DEFAULT_COMPONENTS),
    }
    score = {s: 0 for s in setups}
    wrong_stack = {s: 0 for s in setups}     # hits from monitoring-stack runbooks (distractors)
    for fam, query, ok in DEV:
        print(f"\n[{fam}] {query}")
        for name, kw in setups.items():
            hits = idx.search(query, **kw)
            alerts = {h["alert"] for h in hits}
            good = (not hits) if ok is None else bool(alerts & ok)
            score[name] += good
            wrong_stack[name] += sum(h["component"] not in R.DEFAULT_COMPONENTS for h in hits)
            print(f" {name:30s} {'OK ' if good else 'MISS'}  " + (", ".join(f"{h['alert']}({h['distance']})" for h in hits) or "abstained"))
    print("\nSummary (OK out of 6; distractor hits = chunks from monitoring-stack runbooks):")
    for name in setups:
        print(f" {name:30s} {score[name]}/6   distractor hits: {wrong_stack[name]}")


if __name__ == "__main__":
    main()
