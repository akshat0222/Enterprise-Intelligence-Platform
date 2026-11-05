from app.retrieval.embedder import HashingEmbedder
from app.retrieval.hybrid import keyword_query

INCIDENTS = b"""# Incidents

## Database outage

The primary database ran out of disk space and all writes failed for four hours.

## Payment timeout

Ticket ERR-4471 was raised when the payment gateway timed out under load.
"""

SLA_A = b"# Support SLA\n\nCritical tickets receive a response within 1 hour.\n"
SLA_B = b"# Support policy\n\nCritical tickets receive a response within 4 hours.\n"


def upload(client, headers, name, data):
    r = client.post("/documents", headers=headers, files={"file": (name, data, "text/markdown")})
    assert r.status_code == 202
    return r.json()["id"]


def search(client, headers, query, **extra):
    r = client.post("/search", headers=headers, json={"query": query, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_hashing_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder()
    a, b = e.embed_query("database outage"), e.embed_query("database outage")
    assert a == b and len(a) == 384
    assert abs(sum(x * x for x in a) - 1) < 1e-6


def test_keyword_query_ors_distinct_words():
    assert keyword_query("What about ERR-4471, the error?") == "err | 4471 | error"


def test_search_finds_relevant_chunk_and_stores_embeddings(client, alice):
    from sqlalchemy import text

    from app.db.session import engine

    upload(client, alice, "incidents.md", INCIDENTS)
    with engine.connect() as conn:
        embedded = text("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL")
        assert conn.scalar(embedded) > 0
    out = search(client, alice, "why did the database fail")
    assert out["insufficient_evidence"] is False
    assert "disk space" in out["hits"][0]["content"]
    assert out["hits"][0]["filename"] == "incidents.md"


RUNBOOK = b"""OPERATIONS RUNBOOK
==================

1. DATABASE FAILOVER

Check the replication lag before promoting the replica. Promote it only if lag is under 10 seconds.

2. BACKUPS

Full backups run nightly at 02:00 UTC and are kept for 30 days.
"""


def test_section_heading_is_keyword_searchable(client, alice):
    """The heading words are not in the chunk body, yet a question using them must still find the section."""
    upload(client, alice, "runbook.txt", RUNBOOK)
    out = search(client, alice, "DATABASE FAILOVER")
    assert out["insufficient_evidence"] is False
    top = out["hits"][0]
    assert "replication lag" in top["content"] and top["keyword_rank"] is not None


def test_exact_identifier_is_found_by_keyword_search(client, alice):
    upload(client, alice, "incidents.md", INCIDENTS)
    top = search(client, alice, "ERR-4471")["hits"][0]
    assert "ERR-4471" in top["content"] and top["keyword_rank"] is not None


def test_unrelated_query_trips_relevance_gate(client, alice):
    upload(client, alice, "incidents.md", INCIDENTS)
    assert search(client, alice, "quarterly vegetarian catering budget")["insufficient_evidence"] is True


def test_search_is_scoped_to_the_user(client, alice, bob):
    upload(client, alice, "incidents.md", INCIDENTS)
    out = search(client, bob, "database disk space")
    assert out["hits"] == [] and out["insufficient_evidence"] is True
    assert len(search(client, alice, "database disk space")["hits"]) > 0


def test_document_filter_and_foreign_document_ids(client, alice, bob):
    a_doc = upload(client, alice, "incidents.md", INCIDENTS)
    b_doc = upload(client, bob, "other.md", b"# Other\n\nThe database is fine.\n")
    only_a = search(client, alice, "database", document_ids=[a_doc])["hits"]
    assert only_a and all(h["document_id"] == a_doc for h in only_a)
    assert search(client, alice, "database", document_ids=[b_doc])["hits"] == []  # someone else's id yields nothing


def test_conflicting_documents_both_surface(client, alice):
    a, b = upload(client, alice, "sla_a.md", SLA_A), upload(client, alice, "sla_b.md", SLA_B)
    ids = {h["document_id"] for h in search(client, alice, "critical ticket response time")["hits"]}
    assert {a, b} <= ids


def test_search_requires_auth_and_validates(client, alice):
    assert client.post("/search", json={"query": "abc"}).status_code == 401
    assert client.post("/search", headers=alice, json={"query": "a"}).status_code == 422


def test_local_model_is_semantic_and_384d():
    from app.retrieval.embedder import LocalEmbedder

    q, near, far = LocalEmbedder().embed_documents(
        ["the server ran out of disk space", "storage volume was full", "vegetarian catering menu"]
    )

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert len(q) == 384 and dot(q, near) > dot(q, far) + 0.2
