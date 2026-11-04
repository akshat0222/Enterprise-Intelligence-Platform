"""Build eval/report.md from eval/results/*.json.  Run: python -m eval.report"""

import json
import pathlib
import re
import statistics
from collections import defaultdict

HERE = pathlib.Path(__file__).parent
RESULTS = HERE / "results"

LABELS = {
    "vector_only": "Vector only",
    "keyword_only": "Keyword only (Postgres full-text)",
    "hybrid_500": "Hybrid (vector + keyword, RRF), chunk 500",
    "hybrid_rerank": "Hybrid + cross-encoder rerank, chunk 500",
    "hybrid_chunk300": "Hybrid, chunk 300",
    "hybrid_chunk800": "Hybrid, chunk 800",
}
ORDER = list(LABELS)
METRICS = [("hit@1", "Hit@1"), ("hit@3", "Hit@3"), ("recall@8", "Recall@8"), ("mrr", "MRR"),
           ("gate_refusal_unanswerable", "Unanswerable stopped by gate")]  # fmt: skip


def load() -> tuple[dict[str, list[dict]], dict | None]:
    groups: dict[str, list[dict]] = defaultdict(list)
    e2e = None
    for path in sorted(RESULTS.glob("*.json")):
        if path.name.endswith((".answers.json", ".judgements.json")):  # caches, not results
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["name"].startswith("e2e_"):
            e2e = data
        else:
            groups[re.sub(r"_r\d+$", "", data["name"])].append(data)
    return groups, e2e


def span(values: list[float]) -> str:
    mean = statistics.mean(values)
    return f"{mean:.2f}" if len(values) == 1 or max(values) == min(values) else f"{mean:.2f} ({min(values):.2f}–{max(values):.2f})"


def retrieval_table(groups: dict[str, list[dict]], names: list[str]) -> str:
    head = "| Configuration | Runs | Chunks in corpus | " + " | ".join(m[1] for m in METRICS) + " |"
    lines = [head, "|" + "---|" * (3 + len(METRICS))]
    for name in names:
        runs = groups.get(name)
        if not runs:
            continue
        cells = [span([r["retrieval"][key] for r in runs]) for key, _ in METRICS]
        lines.append(f"| {LABELS[name]} | {len(runs)} | {runs[0]['corpus']['chunks']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def pc(x) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def build() -> str:
    groups, e2e = load()
    out = ["# Evaluation report", ""]
    out.append((HERE / "methodology.md").read_text(encoding="utf-8").strip())
    out.append("")

    if e2e:
        s = e2e["end_to_end"]["summary"]
        cfg = e2e["config"]
        out += [
            "## 1. End-to-end results",
            "",
            f"Configuration: `{cfg['mode']}` retrieval, chunk size {cfg['chunk_tokens']}, embedder `{cfg['embedding']}`, "
            f"relevance gate min_similarity {cfg['min_similarity']}, answers by Claude (Sonnet 5.5 for answers, Haiku 5.5 for "
            f"routing/verification). {s['n']} questions.",
            "",
            "Judged by: " + ", ".join(f"`{m}` ×{n}" for m, n in s["judged_by"].items())
            + ". Judgements marked “same family as answerer” were made by Claude because every Gemini judge had exhausted its "
            "free-tier daily quota; they are included for completeness but carry the self-preference caveat.",
            "",
            "| Metric | Result |", "|---|---|",
            f"| Answer correctness (LLM judge, answerable questions; correct=1, partial=0.5) | {pc(s['answer_correctness'])} |",
            f"| Numeric exact match (questions with expected numbers) | {pc(s['numeric_exact_match'])} |",
            f"| Citation validity (answered questions: cited, quotes found in sources) | {pc(s['citation_validity'])} |",
            f"| Mean groundedness (LLM claim check) | {s['mean_groundedness']} |",
            f"| Unanswerable questions correctly refused | {pc(s['unanswerable_refused'])} |",
            f"| Conflict questions where the conflict was flagged | {pc(s['conflicts_flagged'])} |",
            f"| Prompt injection resisted (canary word not in answer) | {pc(s['injection_resisted'])} |",
            f"| Latency p50 / p95 | {s['latency_p50_ms'] / 1000:.1f} s / {s['latency_p95_ms'] / 1000:.1f} s |",
            f"| Average tokens per question | {s['avg_tokens']:,} |",
            f"| Average estimated cost per question | ${s['avg_cost_usd']:.4f} |",
            f"| Questions that used the fallback provider | {s['fallback_questions']} |",
            "",
            "### Correctness by category",
            "",
            "| Category | Questions | Judge score |", "|---|---|---|",
        ]  # fmt: skip
        counts: dict[str, int] = defaultdict(int)
        for r in e2e["end_to_end"]["per_question"]:
            counts[r["category"]] += 1
        for cat, score in s["answer_correctness_by_category"].items():
            out.append(f"| {cat} | {counts[cat]} | {pc(score)} |")

        misses = [r for r in e2e["end_to_end"]["per_question"] if r["score"] < 1 or r.get("canary_leaked")]
        out += ["", "### Questions that were not fully correct", ""]
        if not misses:
            out.append("None.")
        for r in misses:
            out += [f"- **{r['id']}** ({r['category']}, score {r['score']}, status `{r['status']}`): {r['judge']}",
                    f"  - Answer: “{r['answer'][:300].replace(chr(10), ' ')}…”"]  # fmt: skip
        out.append("")

    out += [
        "## 2. Retrieval experiments", "",
        "Document-level metrics over the 24 answerable questions (follow-ups use their standalone form), top-8 chunks. "
        "Each configuration was run several times with a freshly ingested corpus; cells show the mean and (min–max) so "
        "run-to-run noise is visible. Ties in ranking are broken by random chunk ids, which moves Hit@1 by up to one "
        "question (≈0.04).", "",
        "### Retrieval strategy", "", retrieval_table(groups, ["vector_only", "keyword_only", "hybrid_500", "hybrid_rerank"]),
        "", "### Chunk size", "", retrieval_table(groups, ["hybrid_chunk300", "hybrid_500", "hybrid_chunk800"]), "",
    ]  # fmt: skip

    base = groups.get("hybrid_500")
    if base and "gate_sweep" in base[0]:
        out += ["## 3. Relevance gate threshold", "",
                "Best-hit cosine similarity decides whether any evidence is relevant. The sweep uses the hybrid retrieval "
                "results above (keyword rank threshold fixed at "
                f"{base[0]['config']['min_keyword_rank']}). “Keyword rescue” = a chunk also passes if its keyword rank "
                "clears the threshold.", "",
                "| min_similarity | Unanswerable refused | Answerable wrongly refused | Unanswerable refused (similarity only) | "
                "Answerable wrongly refused (similarity only) |", "|---|---|---|---|---|"]  # fmt: skip
        for g in base[0]["gate_sweep"]:
            out.append(f"| {g['min_similarity']} | {pc(g['refuse_unanswerable'])} | {pc(g['false_refusal'])} | "
                       f"{pc(g['refuse_unanswerable_sim_only'])} | {pc(g['false_refusal_sim_only'])} |")  # fmt: skip
        out.append("")

    analysis = HERE / "analysis.md"
    if analysis.exists():
        out += [analysis.read_text(encoding="utf-8").strip(), ""]
    return "\n".join(out)


if __name__ == "__main__":
    (HERE / "report.md").write_text(build(), encoding="utf-8")
    print("wrote eval/report.md")
