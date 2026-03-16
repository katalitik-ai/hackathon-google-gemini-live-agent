"""
rag_tools.py — Elasticsearch retrieval functions v29.0

Changes vs v17.0.1:
  get_data(): new voice_only: bool = False parameter
    When voice_only=True → skip _fetch_search_docs() (the legalitik-searching roundtrip)
    → Saves ~200-400ms for voice-only queries (regulation_query path)
    → Documents list will be empty; context string is still built
    → Used by _get_data_voice() in function_calling.py
"""
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from itertools import groupby
from typing import Optional

from dateutil.relativedelta import relativedelta
from elasticsearch import Elasticsearch, ConnectionError as ESConnectionError

from app.config import settings

log = logging.getLogger(__name__)


# ── Elasticsearch client (module-level singleton) ─────────────────────────────

def _build_es_client() -> Elasticsearch:
    return Elasticsearch(
        settings.ES_HOST,
        basic_auth=(settings.ES_USER, settings.ES_PASS),
        request_timeout=settings.ES_TIMEOUT,
        retry_on_timeout=True,
        max_retries=settings.ES_MAX_RETRIES,
    )


es = _build_es_client()

# BUG-FIX #6: Module-level singleton is never recreated after a connection
# drop, causing all subsequent ES queries to silently return 0 results.
# Solution: a lightweight accessor that pings ES and rebuilds the client
# on failure. The ping uses `es.info()` which is a cheap cluster-info call.
_es_lock = threading.Lock()


def _get_es() -> Elasticsearch:
    """
    Return the ES client, recreating it if the connection has dropped.
    Uses a double-checked lock so recreation happens at most once at a time.
    """
    global es
    try:
        es.info(request_timeout=2)   # fast liveness check
        return es
    except Exception:
        with _es_lock:
            try:
                es.info(request_timeout=2)  # re-check inside lock
                return es
            except Exception:
                log.warning("ES connection lost — recreating client")
                es = _build_es_client()
                return es


# ── Regulator name normalisation map ─────────────────────────────────────────

REGULATOR_NAMES: dict[str, str] = {
    "bi":         "Bank Indonesia",
    "ojk":        "Otoritas Jasa Keuangan",
    "lps":        "Lembaga Penjamin Simpanan",
    "kemenkeu":   "Kementerian Keuangan",
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _pick_content(src: dict) -> str:
    return (
        src.get("isi_ketentuan_umum")
        or src.get("isi")
        or src.get("content")
        or src.get("summary")
        or ""
    )


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _title_case_id(text: str) -> str:
    if not text:
        return ""
    lowercase_exceptions = {
        "dan", "di", "ke", "dari", "yang", "untuk",
        "dengan", "atau", "oleh", "pada",
    }
    words = text.lower().split()
    return " ".join(
        w if (i > 0 and w in lowercase_exceptions) else w.capitalize()
        for i, w in enumerate(words)
    )


def _format_legal_text(text: str) -> str:
    return text.strip() if text else ""


def _parse_summary_fields(summary_raw) -> tuple[str, str, str, str]:
    summary_str = summary_scope = summary_purpose = summary_notes = ""

    if isinstance(summary_raw, list):
        for item in summary_raw:
            title = item.get("title", "").lower()
            value = item.get("value", "")
            if "ruang lingkup" in title:
                summary_scope = value
            elif "tujuan" in title:
                summary_purpose = value
            elif "tambahan" in title:
                summary_notes = value
            else:
                summary_str += value + "\n"
        summary_str = summary_str.strip()
    else:
        summary_str = summary_raw or ""

    return summary_str, summary_scope, summary_purpose, summary_notes


def _transform_search_hits(hits: list, query: str, score_map: dict | None = None) -> list:
    """
    Transform raw ES hits (from legalitik-searching index) into
    structured document objects for the frontend repository panel.
    """
    documents = []
    for rank, hit in enumerate(hits, start=1):
        src   = hit["_source"]
        about = src.get("tentang", "")

        if about and re.search(r"\bperubahan\s+atas\b", about, re.IGNORECASE):
            continue

        status_entries = [
            {"name": "Mencabut",     "regulations": src.get("status_mencabut",     [])},
            {"name": "Mengubah",     "regulations": src.get("status_merubah",       [])},
            {"name": "Melaksanakan", "regulations": src.get("status_melaksanakan", [])},
        ]
        statuses = []
        for entry in status_entries:
            if not entry["regulations"]:
                continue
            regs_with_year = []
            for reg_name in entry["regulations"]:
                year_match = re.search(r"tahun\s+(\d{4})", reg_name, re.IGNORECASE)
                regs_with_year.append({
                    "name": reg_name,
                    "year": int(year_match.group(1)) if year_match else None,
                })
            statuses.append({"name": entry["name"], "regulations": regs_with_year})

        summary_raw = src.get("summary", "")
        if isinstance(summary_raw, list):
            ai_summary = " ".join(item.get("value", "") for item in summary_raw)
        else:
            ai_summary = summary_raw or ""

        kind2 = src.get("kind2", "")
        doc = {
            "id":            rank,
            "doc_id":        src.get("id"),
            "kind2":         kind2,
            "title":         _title_case_id(src.get("kind2", "-")),
            "source":        _title_case_id(src.get("kind", "-")),
            "rawDate":       src.get("disahkan_datetime") or None,
            "formattedDate": src.get("diundangkan") or None,
            "snippet":       _title_case_id(about),
            "keyword":       query,
            "keywords":      src.get("contextual_keywords", []),
            "file_path":     src.get("file"),
            "metadata": {
                "instruction":   src.get("total_instruksi", 0),
                "total_chapter": src.get("total_bab", 0),
                "total_section": src.get("total_pasal", 0),
                "total_verse":   src.get("total_ayat", 0),
            },
            "statuses":   statuses,
            "aiSummary":  ai_summary,
            "references": src.get("terkait", []),
            "status":     "-",
        }
        if score_map is not None:
            doc["relevance_score"] = score_map.get(kind2, 0.0)
        documents.append(doc)
    return documents


def _fetch_search_docs(kind2_list: list) -> list:
    """
    Fetch full document metadata from legalitik-searching for a list of kind2 IDs.
    Preserves the input order.
    """
    if not kind2_list:
        return []
    body = {
        "size": len(kind2_list),
        "query": {"terms": {"kind2.keyword": kind2_list}},
        "_source": {
            "excludes": [
                "embedding", "content_vector",
                "isi", "isi_ketentuan_umum", "isi_pasal", "isi_ayat",
                "isi_instruksi", "content", "pasal_demi_pasal",
            ]
        },
    }
    hits    = _get_es().search(index=settings.ES_INDEX_SEARCH, body=body)["hits"]["hits"]
    hit_map = {h["_source"].get("kind2"): h for h in hits}
    return [hit_map[k] for k in kind2_list if k in hit_map]


# ── ES query builder ──────────────────────────────────────────────────────────

def _make_query_body(query: str, filters: list, size: int) -> dict:
    # BUG #3 FIX: kind2 (document title e.g. "Peraturan Bank Indonesia Nomor 7 Tahun 2025")
    # and kind (document type e.g. "Peraturan Bank Indonesia") MUST be searched so that
    # keyword queries like "makroprudensial" or doc-name queries actually hit the right docs.
    # Previously these fields were absent → keyword search missed the document title entirely.
    return {
        "size": size,
        "query": {
            "bool": {
                "should": [{
                    "multi_match": {
                        "query":  query,
                        "fields": [
                            "kind2^5",              # document title — highest boost
                            "kind^3",               # document type
                            "tentang^4",            # subject / about
                            "isi^2",
                            "isi_ketentuan_umum^2",
                            "content^2",
                            "summary",
                            "subject_hukum_ketum",
                            "keyword_tentang^2",
                        ],
                        "type": "best_fields",
                        "minimum_should_match": "1",
                    }
                },
                {
                    # BUG #3 FIX (secondary): phrase match on kind2 for exact doc-name queries
                    # Gives a strong boost when the query is an exact or near-exact doc title.
                    "match_phrase": {
                        "kind2": {
                            "query": query,
                            "boost": 8,
                            "slop":  3,
                        }
                    }
                }],
                "filter": filters,
            }
        },
        "_source": {
            "excludes": ["embedding", "content_vector", "pasal_demi_pasal"]
        },
    }


# ── Tool 1: get_data ──────────────────────────────────────────────────────────

def get_data(
    query:      str,
    query_en:   Optional[str] = None,
    instansi:   Optional[str] = None,
    jenis:      Optional[str] = None,
    tahun:      Optional[str] = None,
    top_k:      int           = settings.TOP_K_DOCS,
    voice_only: bool          = False,   # v18 NEW: skip _fetch_search_docs when True
) -> dict:
    """
    Bilingual BM25 search on the legalitik-knowledge-based index.

    voice_only=True  → skip the legalitik-searching roundtrip (_fetch_search_docs).
                       Context string is still built for Gemini injection.
                       Documents list will be empty (not needed for voice answers).
                       Saves ~200-400ms per query.

    voice_only=False → full behaviour (context + UI documents). Default.
    """
    try:
        filters: list = []
        if instansi:
            filters.append({"term": {"instansi.keyword": instansi}})
        if jenis:
            filters.append({"term": {"kind.keyword": jenis}})
        if tahun:
            filters.append({"term": {"tahun.keyword": tahun}})

        chunk_size   = settings.TOP_K_CHUNKS
        index_header = {"index": settings.ES_INDEX}

        use_bilingual = bool(
            query_en
            and query_en.strip()
            and query_en.strip().lower() != query.strip().lower()
        )

        if use_bilingual:
            responses = _get_es().msearch(body=[
                index_header, _make_query_body(query,    filters, chunk_size),
                index_header, _make_query_body(query_en, filters, chunk_size),
            ])["responses"]
            hits_id = responses[0].get("hits", {}).get("hits", [])
            hits_en = responses[1].get("hits", {}).get("hits", [])
            log.info("get_data bilingual: q_id=%r → %d hits | q_en=%r → %d hits",
                     query, len(hits_id), query_en, len(hits_en))
        else:
            hits_id = _get_es().search(
                index=settings.ES_INDEX,
                body=_make_query_body(query, filters, chunk_size),
            )["hits"]["hits"]
            hits_en = []
            log.info("get_data single: q_id=%r → %d hits", query, len(hits_id))

        # Merge + deduplicate
        seen_kind2: list[str]        = []
        score_map:  dict[str, float] = {}
        chunks_map: dict[str, dict]  = {}

        def _ingest_hits(hits: list, score_boost: float) -> None:
            for hit in hits:
                src   = hit["_source"]
                key   = src.get("kind2", "unknown")
                score = (hit.get("_score") or 0.0) * score_boost
                if key not in chunks_map:
                    seen_kind2.append(key)
                    chunks_map[key] = {
                        "about":  src.get("tentang", ""),
                        "chunks": [],
                    }
                    score_map[key] = score
                else:
                    if score > score_map[key]:
                        score_map[key] = score
                chunks_map[key]["chunks"].append(_pick_content(src))

        _ingest_hits(hits_id, score_boost=1.2)
        _ingest_hits(hits_en, score_boost=1.0)

        ranked_kind2 = sorted(
            seen_kind2,
            key=lambda k: score_map.get(k, 0.0),
            reverse=True,
        )[:top_k]

        # Build context string
        context_parts: list[str] = []
        for kind2 in ranked_kind2:
            text_chunks = [c for c in chunks_map[kind2]["chunks"] if c]
            if text_chunks:
                about = chunks_map[kind2]["about"]
                body  = "\n".join(text_chunks[:3])
                context_parts.append(f"[{kind2.upper()}]\n{about}\n{body}")

        # ── v18: VOICE ONLY → skip searching index roundtrip ──────────────
        if voice_only:
            log.info("get_data voice_only: skipping _fetch_search_docs → saved 1 ES roundtrip")
            # BUG #5 FIX: total = len(ranked_kind2) is the count from the knowledge-based
            # index (content chunks). In full mode, total = len(documents) which is the count
            # after the searching-index join and _transform_search_hits filter.
            # These CAN differ because the two indices are not always in sync.
            # For voice-only, ranked_kind2 count is the right number to tell the LLM —
            # it reflects exactly what content was found and will be injected as context.
            # Panel is never opened in voice-only path so there is no visible mismatch.
            return {
                "documents": [],
                "context":   "\n\n---\n\n".join(context_parts),
                "total":     len(ranked_kind2),
            }

        # ── FULL MODE: fetch UI metadata from searching index (parallel) ──
        with ThreadPoolExecutor(max_workers=1) as _pool:
            future_docs = _pool.submit(_fetch_search_docs, ranked_kind2)
            search_hits = future_docs.result()

        documents = _transform_search_hits(search_hits, query, score_map=score_map)

        return {
            "documents": documents,
            "context":   "\n\n---\n\n".join(context_parts),
            "total":     len(documents),
        }

    except ESConnectionError as exc:
        log.error("ES connection error in get_data: %s", exc)
        return {"documents": [], "context": "", "total": 0,
                "error": "Elasticsearch is unavailable."}
    except Exception as exc:
        log.exception("Unexpected error in get_data")
        return {"documents": [], "context": "", "total": 0, "error": str(exc)}


# ── Tool 2: detail ────────────────────────────────────────────────────────────

def detail(kind2: str) -> dict:
    """
    Fetch the metadata summary of a single regulation from legalitik-searching.
    Always returns full metadata for the detail panel.
    """
    try:
        body = {
            "size": 1,
            "query": {"term": {"kind2.keyword": kind2}},
            "_source": {
                "excludes": [
                    "embedding", "content_vector",
                    "isi", "isi_ketentuan_umum", "isi_pasal", "isi_ayat",
                    "isi_instruksi", "content", "pasal_demi_pasal",
                ]
            },
        }
        hits = _get_es().search(index=settings.ES_INDEX_SEARCH, body=body)["hits"]["hits"]
        if not hits:
            return {"error": f"Document '{kind2}' not found."}

        src = hits[0]["_source"]
        summary, scope, purpose, notes = _parse_summary_fields(src.get("summary", ""))

        return {
            "kind2":                 src.get("kind2", ""),
            "judul":                 src.get("tentang", ""),
            "total_bab":             src.get("total_bab", 0),
            "total_pasal":           src.get("total_pasal", 0),
            "total_ayat":            src.get("total_ayat", 0),
            "total_instruksi":       src.get("total_instruksi", 0),
            "summary":               summary,
            "summary_ruang_lingkup": scope,
            "summary_tujuan":        purpose,
            "summary_tambahan":      notes,
            "status_detail": {
                "melaksanakan": src.get("status_melaksanakan", []),
                "mencabut":     src.get("status_mencabut",     []),
            },
        }

    except ESConnectionError as exc:
        log.error("ES connection error in detail: %s", exc)
        return {"error": "Elasticsearch is unavailable."}
    except Exception as exc:
        log.exception("Unexpected error in detail")
        return {"error": str(exc)}


# ── Tool 3: get_newest_documents ──────────────────────────────────────────────

def get_newest_documents(
    limit:       int           = settings.NEWEST_DOCS_LIMIT,
    months_back: int           = settings.NEWEST_DOCS_MONTHS_BACK,
    regulator:   Optional[str] = None,
) -> dict:
    """
    Fetch recently added documents from legalitik-searching.
    NOT part of the real-time voice RAG pipeline.
    """
    try:
        now            = datetime.now()
        date_threshold = now - relativedelta(months=months_back)

        es_query: dict = {"match_all": {}}
        if regulator:
            normalized_name = REGULATOR_NAMES.get(regulator.lower(), regulator)
            es_query = {
                "bool": {
                    "filter": [{"term": {"instansi.keyword": normalized_name}}]
                }
            }

        body = {
            "size": 50,
            "query": es_query,
            "sort": [{
                "_script": {
                    "type":  "number",
                    "order": "desc",
                    "script": {
                        "lang":   "painless",
                        "source": """
                            def ts = doc['timestamp.keyword'].value;
                            if (ts == null) return 0;
                            def fmt = DateTimeFormatter.ofPattern('dd/MM/yyyy HH:mm:ss');
                            return LocalDateTime.parse(ts, fmt)
                                               .atZone(ZoneId.of('UTC'))
                                               .toInstant()
                                               .toEpochMilli();
                        """,
                    },
                }
            }],
            "_source": {
                "excludes": [
                    "embedding", "content_vector",
                    "isi", "isi_ketentuan_umum", "isi_pasal", "isi_ayat",
                    "isi_instruksi", "content", "pasal_demi_pasal",
                ]
            },
        }

        hits = _get_es().search(index=settings.ES_INDEX_SEARCH, body=body)["hits"]["hits"]

        docs_by_kind2: dict = {}
        for hit in hits:
            src    = hit["_source"]
            kind2  = src.get("kind2")
            ts_str = src.get("timestamp")
            if not kind2 or not ts_str or kind2 in docs_by_kind2:
                continue
            try:
                doc_date = datetime.strptime(ts_str, "%d/%m/%Y %H:%M:%S")
                if doc_date >= date_threshold:
                    docs_by_kind2[kind2] = {"data": src, "parsed_date": doc_date}
            except ValueError:
                continue

        newest: list = []
        for kind2, info in docs_by_kind2.items():
            src      = info["data"]
            doc_date = info["parsed_date"]
            newest.append({
                "id":            src.get("id"),
                "source":        _title_case_id(src.get("kind", "")),
                "title":         _title_case_id(src.get("kind2", "")),
                "about":         _title_case_id(src.get("tentang", "")),
                "total_chapter": _safe_int(src.get("total_bab",   0)),
                "total_section": _safe_int(src.get("total_pasal", 0)),
                "total_verse":   _safe_int(src.get("total_ayat",  0)),
                "publishedDate": src.get("diundangkan")         or None,
                "approvedDate":  src.get("disahkan_datetime")   or None,
                "addedToSystem": src.get("timestamp"),
                "_sortDate":     doc_date,
            })

        newest.sort(key=lambda x: x["_sortDate"], reverse=True)
        for doc in newest:
            doc.pop("_sortDate")

        result = newest[:limit]
        return {
            "data":       result,
            "total":      len(result),
            "totalFound": len(newest),
            "dateRange": {
                "from":       date_threshold.strftime("%d/%m/%Y"),
                "to":         now.strftime("%d/%m/%Y"),
                "monthsBack": months_back,
            },
        }

    except ESConnectionError as exc:
        log.error("ES connection error in get_newest_documents: %s", exc)
        return {"data": [], "total": 0, "totalFound": 0,
                "error": "Elasticsearch is unavailable."}
    except Exception as exc:
        log.exception("Unexpected error in get_newest_documents")
        return {"data": [], "total": 0, "totalFound": 0, "error": str(exc)}


# ── Tool 4: deep_search ───────────────────────────────────────────────────────

def deep_search(kind2: str) -> dict:
    """
    Fetch the complete hierarchical content of a regulation document.
    Returns metadata + structured content tree for the DocPanel.
    """
    try:
        body = {
            "size": 2000,
            "query": {
                "match": {
                    "kind2": {"query": kind2, "operator": "and"}
                }
            },
            "_source": [
                "id", "bab", "judul_bab", "pasal", "ayat",
                "isi", "kind2", "tentang", "kind",
                "status_mencabut", "status_merubah", "diundangkan",
                "menimbang", "mengingat", "menetapkan",
            ],
        }
        hits = _get_es().search(index=settings.ES_INDEX, body=body)["hits"]["hits"]
        if not hits:
            return {"error": f"Document '{kind2}' not found."}

        return _build_document_hierarchy(hits)

    except ESConnectionError as exc:
        log.error("ES connection error in deep_search: %s", exc)
        return {"error": "Elasticsearch is unavailable."}
    except Exception as exc:
        log.exception("Unexpected error in deep_search")
        return {"error": str(exc)}


# ── Hierarchy builder ─────────────────────────────────────────────────────────

def _build_document_hierarchy(es_hits: list) -> dict:
    """Organise raw ES hits into the hierarchical document structure."""
    base_src       = es_hits[0]["_source"]
    structure: list = []

    for subtype, label, field in [
        ("title", "Judul",   "kind2"),
        ("about", "Tentang", "tentang"),
    ]:
        if base_src.get(field):
            structure.append({
                "type":    "header",
                "subtype": subtype,
                "label":   label,
                "content": _title_case_id(base_src[field]),
            })

    for subtype, label, field in [
        ("consideration", "Menimbang",  "menimbang"),
        ("legal_basis",   "Mengingat",  "mengingat"),
        ("to_enact",      "Menetapkan", "menetapkan"),
    ]:
        raw_value = base_src.get(field)
        if not raw_value or str(raw_value) in {"None", "null", ""}:
            continue

        raw_text = _format_legal_text(str(raw_value))

        if subtype == "to_enact":
            raw_text = re.sub(
                r"^\s*menetapkan\s*:?\s*", "",
                raw_text,
                flags=re.IGNORECASE,
            )
            structure.append({
                "type":    "preamble",
                "subtype": subtype,
                "label":   label,
                "content": raw_text.strip(),
            })
            continue

        lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
        structure.append({
            "type":     "preamble",
            "subtype":  subtype,
            "label":    label,
            "content":  raw_text,
            "contents": [
                {"point": str(i), "content": line}
                for i, line in enumerate(lines, 1)
            ],
        })

    sorted_hits = sorted(
        es_hits,
        key=lambda x: (
            _safe_int(x["_source"].get("bab",   0)),
            _safe_int(x["_source"].get("pasal", 0)),
            _safe_int(x["_source"].get("ayat",  0)),
        ),
    )

    for chapter_key, chapter_group in groupby(
        sorted_hits,
        key=lambda x: x["_source"].get("bab", "Unknown"),
    ):
        chapter_items = list(chapter_group)
        chapter_title = chapter_items[0]["_source"].get("judul_bab", "")
        is_general_provisions = (
            "ketentuan umum" in chapter_title.lower() if chapter_title else False
        )

        articles: list = []
        for article_key, article_group in groupby(
            chapter_items,
            key=lambda x: x["_source"].get("pasal", ""),
        ):
            article_items = list(article_group)
            content_list:  list = []

            for seq_idx, item in enumerate(article_items, 1):
                verse_val = item["_source"].get("ayat")
                has_verse = (
                    verse_val is not None
                    and str(verse_val).strip().lower() not in {"", "none", "null"}
                )
                verse_obj: dict = {"id": item["_source"].get("id")}
                if has_verse or is_general_provisions:
                    verse_obj["point"] = str(seq_idx)
                verse_obj["verse"]   = verse_val
                verse_obj["content"] = _format_legal_text(
                    item["_source"].get("isi") or ""
                )
                content_list.append(verse_obj)

            articles.append({"number": article_key, "contents": content_list})

        structure.append({
            "type":           "body",
            "chapter_number": chapter_key,
            "chapter_title":  _title_case_id(chapter_title) if chapter_title else "",
            "sections":       articles,
        })

    return {
        "metadata": {
            "title":          _title_case_id(base_src.get("kind2", "")),
            "about":          _title_case_id(base_src.get("tentang", "")),
            "type":           _title_case_id(base_src.get("kind", "")),
            "date":           base_src.get("diundangkan"),
            "status":         "-",
            "total_chapters": sum(1 for n in structure if n["type"] == "body"),
        },
        "structure": structure,
    }