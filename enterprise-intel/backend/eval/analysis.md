## 4. What the results show

**End to end the system does well on this corpus: 98% judged correctness, with every numeric answer, citation, refusal,
conflict flag and injection test passing.** One question was only partly correct (`ms-4`, below). Treat the headline as
"works on a small, clean, synthetic corpus", not as a general accuracy claim (see limitations).

**The retrieval experiments cannot separate the strategies.** The corpus is 10 documents and 34 chunks, so document-level
Hit@3 and Recall@8 are 1.00 for every configuration. Hit@1 and MRR differ by at most one question (0.04), which is also the
size of the run-to-run noise measured by repeating each configuration three times (keyword-only Hit@1 ranged 0.83–0.92 with
no change at all). So these data do **not** show hybrid beating vector-only, reranking helping, or any chunk size being best.
What they do show:
- Keyword-only search is competitive here because the questions contain distinctive terms (INC-102, T-1007, "72 hours").
  Hybrid is kept for design reasons (exact identifiers plus semantic matching in one query path), not because this evaluation
  proves it; a larger corpus with paraphrased questions would be needed to show the difference.
- The cross-encoder reranker gave no measurable gain and costs an extra model download and latency, so it stays off by default
  (`RERANK_ENABLED=false`).
- Chunk sizes 300, 500 and 800 behave the same because the documents are short; 500 stays the default.

**The relevance gate needed tuning, and that is where the evaluation changed the system.** With the original threshold
(0.2) the gate stopped only 25% of unanswerable questions before generation. The sweep shows that every answerable question
has a best-hit similarity of at least 0.515, while three of four unanswerable questions stay under 0.30, so any threshold
between about 0.3 and 0.5 gives 75% refusal with no false refusals; the default is now 0.4. The fourth unanswerable question
("Which programming language is the Atlas billing service written in?") scores 0.57 because "Atlas" appears in the corpus, so
the gate cannot stop it; the answering model then correctly returned `insufficient_evidence`. Two layers of protection both
matter. This threshold was tuned on the same questions it is evaluated on (see limitations).

**Prompt injection:** both questions about the poisoned vendor note were answered correctly (Paynova, 555-0142). The model
flagged the injected notice and treated it as data. My first metric counted any mention of the trigger word as a failure,
which wrongly failed an answer that quoted the notice while refusing it; the metric now counts an answer as compromised only
if it starts with the injected word. Passing two questions against one injected string is weak evidence, not proof of robustness.

**The one failure (`ms-4`, "summarise the fixes recommended across the quarterly incident reports").** The agent's search for
"recommended fixes and remediation actions" returned the Q2 postmortem actions but not Q1's or Q3's, so the report said only
the Q2 report recommends anything and listed three of the seven fixes. This is retrieval, not generation: querying "postmortem
actions recommendations" ranks all three postmortem chunks first, but the user's vocabulary ("fixes") shares almost no words with
the documents' vocabulary ("actions"), so the missing chunks never reach the candidate pool. A planner instruction to use a
wider `top_k` did not fix it. The plan's remedy, multi-query expansion (several rewrites of the query merged by RRF), is not
implemented; per-document retrieval for "across all documents" questions would also help.

**A gap the dataset did not catch (found by manual testing).** Asking "what is database failover" against the runbook was
refused as "not found" although the section is titled DATABASE FAILOVER. The embedding already included the heading, but the
keyword index covered only the body, which never uses those words, and a short definitional query scores low on MiniLM
similarity alone. The keyword index now includes the heading, weighted above the body (migration 0004), with a regression test.
The retrieval numbers above are unchanged by the fix (re-run: Hit@1 0.92, Hit@3 1.00, MRR 0.94); the end-to-end run was not
repeated. The dataset has no question that uses only a section title, so it could not have shown this.

**Cost and latency** (estimated prices): p50 6.1 s and p95 22.7 s per question (the agent path is the slow one, about 24 s),
about 6.8k tokens and $0.02 per question on average, no provider fallbacks.

**Changes made because of the evaluation:** relevance gate default 0.2 → 0.4; list items no longer merged into one paragraph
by the PDF text cleaner; the numeric check accepts number words ("three"); the judge may never silently fall back to the
answerer's model family.

## 5. Limitations

- **Small, synthetic, single-author data.** 28 questions over 10 documents, written by the same assistant that built the
  system. The questions may favour what the system is good at, and the ground truth was not independently reviewed.
- **Judge bias and coverage.** 18 judgements were made by Gemini 2.5 Flash-Lite (a different family from the answerer). Gemini's
  free tier has a small daily quota per model and all three models tried (3 Flash preview, 2.5 Flash, 2.5 Flash-Lite) ran out,
  so the other 10 (`cf-3`, `un-1`–`un-4`, `fu-1`–`fu-3`, `pi-1`, `pi-2`) were judged by Claude Haiku and are labelled as
  same-family. Most of those are also decided by deterministic checks (refusal status, conflict flag, canary word). To upgrade
  them, re-run `python -m eval.run --name e2e_hybrid_500 --reuse-answers` after the quota resets; same-family entries are
  discarded and redone by Gemini automatically.
- **Thresholds tuned in-sample.** The relevance gate was chosen using the same questions it is scored on; there is no held-out
  split. Real corpora, short queries and typos will score lower similarities.
- **Retrieval is scored at document level**, not chunk level, and ranking ties are random (hence the repeated runs).
- **No aggregation over tables.** The CSV questions are lookups and schema statistics (min/max). "How many critical tickets" or
  "average resolution time" would fail with retrieval alone and need a deterministic aggregation tool.
- **No OCR** (scanned PDFs are rejected), **English only**, and cost figures use estimated prices, not billing data.
- RAGAS was not used; equivalent metrics are custom scripts.
