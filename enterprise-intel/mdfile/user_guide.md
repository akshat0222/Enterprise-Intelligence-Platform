# User guide: using the Enterprise Intelligence Platform

This guide follows one person through a real session: sign up, load documents, ask questions, run an agent task, and read
the usage pages. Test documents and the exact questions to try are included.

## 1. Start the application

```bash
docker compose up --build
```

| What | Where |
|---|---|
| Application | http://localhost:3000 |
| API documentation (Swagger) | http://localhost:5500/docs |

There is no preset account. You create your own.

## 2. Create an account and sign in

1. Open http://localhost:3000/register, enter an email and a password, and submit.
2. Sign in at http://localhost:3000/login. Sign out is at the top right.

Each account sees only its own documents, conversations and usage. A second account cannot open the first one's data,
which makes a good isolation demo.

## 3. Upload documents (Documents page)

Drag PDF, TXT, Markdown or CSV files onto the page, or use **Choose files**. The test files are in
`backend/eval/corpus/`.

Each row shows a status:

| Status | Meaning |
|---|---|
| `uploaded` | Saved, waiting to be processed |
| `processing` | Text is being extracted, split into chunks and embedded |
| `ready` | Searchable |
| `failed` | Processing failed; the reason is shown, and **Reprocess** retries it |

Things to try:
- **Duplicate upload:** upload the same file twice. The second copy is rejected.
- **Invalid file:** upload a renamed or corrupt file. It is rejected or marked `failed` with a reason.
- **Open a document** to see its chunks, word count, detected headings, top keywords and tables.
- **Delete** removes the document, its chunks and its vectors.

Wait until every file shows `ready` before asking questions.

## 4. Ask questions (Chat page)

Click **+ New chat**, type a question and press **Send**. Under the box you can:
- choose which documents to search (**N documents selected**, with **Clear** to search all), and
- pick a model, or leave **Model: auto** so the system chooses.

Each answer carries a small label showing how it was handled: `direct`, `rag` or `agent`.

The right-hand panel has three tabs:
- **Sources:** the passages the answer used, with the document name, section and page. Citations such as `[S1]` in the
  answer match these.
- **Agent trace:** every step the agent took, with the tool, its arguments, its result and its timing.
- **Details:** model, tokens, latency, and any verification issues or conflicts found.

### Questions to try, in this order

| Goal | Question | What you should see |
|---|---|---|
| Cited answer | "What is the disk usage alert threshold?" | 90 percent, with a source from the runbook |
| Section title only | "database failover" | The failover procedure from the runbook |
| Exact identifier | "What happened in INC-102?" | The incident, cited |
| Conflicting documents | "What is the support response time for critical issues?" | The answer flags that the 2023 and 2024 SLA documents disagree |
| Not in the documents | "Which programming language is the Atlas billing service written in?" | A refusal: not enough evidence |
| Hidden instruction in a document | Ask about the vendor in `vendor_notes.txt` | The planted instruction is ignored and flagged |
| Follow-up | Ask a question, then "what about the other one?" | The answer uses the earlier context and documents |
| Agent task | "Analyze the incident reports and recommend fixes." | Agent trace: search, then analysis, then report |

The agent question is the one the system only partly handles. It tends to return the Q2 incident's fixes but may miss
Q1 and Q3.

## 5. Conversations

Every chat is saved in the left list and is private to your account. Click one to reopen it. The system sends only a
short summary and the last few turns to the model, not the full history, to save tokens.

## 6. Dashboard and Logs

- **Dashboard:** counts of documents and chunks, LLM calls, tokens in and out, estimated cost, latency and error rate,
  split by model and provider, plus recent activity.
- **Logs:** a filterable list of LLM calls (time, task, provider, model, tokens, latency, result) and an activity log of
  uploads, deletions and processing. Filter by model or level, and use **Refresh** and **Load more**.

After a few chats, open both pages and match the numbers to what you asked.

## 7. What it will and will not do

- It answers only from your uploaded documents and says so when the evidence is missing.
- It cites its sources and checks that quoted text exists in them.
- It does not read scanned PDFs (there is no OCR), and it is tuned for English.
- Table questions such as "how many critical tickets" are not computed; the CSV is searched, not aggregated.

## 8. Troubleshooting

| Problem | What to do |
|---|---|
| A document stays in `processing` | Wait a minute, then check `docker compose logs backend` |
| A document shows `failed` | Read the reason, then use **Reprocess** |
| An answer says "not found" | Name the section or use words from the document; very short or vague questions score low |
| Slow first answer | The embedding model loads on first use |
| Page redirects to login | The session expired; sign in again |
