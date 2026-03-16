"""
function_calling.py — Tool executor v29.0

Fixes vs v22.0:
  FIX-4 (Loophole 4 — Over-filtering Weak Query):
    _is_weak_query() sekarang TIDAK pernah menimpa sq yang non-empty ke last_topic.
    Sebelumnya: jika sq dianggap "weak" (hanya 1 token meaningful), sistem otomatis
    mengganti dengan last_topic — bahkan jika last_topic adalah topik berbeda sama sekali.
    Sekarang: sq weak dibiarkan masuk ES. Jika 0 hits, ES returns 0 — bukan
    mengembalikan hasil topik yang SALAH. Fallback ke last_topic hanya jika sq BENAR-BENAR
    kosong setelah semua upaya.

  FIX-5 (Loophole 7 — Specific Doc Identifier / _IS_DOC_ID_RE):
    _IS_DOC_ID_RE diperluas identik dengan _IS_DOC_ID_RE_EXTENDED di classifier.py.
    Menangani "PBI 17" (tanpa "nomor"), "Peraturan BI 17", dan pola lain yang
    sebelumnya gagal dikenali dan menyebabkan ES lookup tak perlu + threshold check fail.
    Setelah normalisasi spoken numbers di classifier (FIX-2), angka seperti "17" sudah
    terkonversi → regex ini lebih sering match.

  FIX-6 (Loophole 3 — Bilingual Prefetch Mismatch):
    intent_to_tool_args() sekarang mengembalikan query_en yang lebih konsisten:
    Jika sq BERBEDA dari kw_id, gunakan sq sebagai query_en. Jika kw_en juga tersedia
    dan berbeda, prioritaskan kw_en (hasil Flash lebih akurat dari manual derivation).
    Ini mengurangi cache miss antara prefetch dan execute_tool.

  Semua logic v22.0 dipertahankan.
"""
import json as _json
import logging
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import settings
from app.rag_tools import get_data, detail, deep_search, get_newest_documents

log = logging.getLogger(__name__)

# ── Legal document type map: acronym → full Indonesian name ───────────────────
_DOC_TYPE_MAP: dict[str, str] = {
    "pbi":       "Peraturan Bank Indonesia",
    "padg":      "Peraturan Anggota Dewan Gubernur",
    "pa":        "Peraturan Anggota Dewan Gubernur",
    "pojk":      "Peraturan Otoritas Jasa Keuangan",
    "seojk":     "Surat Edaran Otoritas Jasa Keuangan",
    "sebi":      "Surat Edaran Bank Indonesia",
    "seki":      "Surat Edaran Bank Indonesia",
    "pp":        "Peraturan Pemerintah",
    "uu":        "Undang-Undang",
    "perpu":     "Peraturan Pemerintah Pengganti Undang-Undang",
    "permen":    "Peraturan Menteri",
    "permenkeu": "Peraturan Menteri Keuangan",
    "pmk":       "Peraturan Menteri Keuangan",
    "kepmen":    "Keputusan Menteri",
}

_TOPIC_MAP: dict[str, str] = {
    "bi":       "Bank Indonesia",
    "ojk":      "Otoritas Jasa Keuangan",
    "lps":      "Lembaga Penjamin Simpanan",
    "kemenkeu": "Kementerian Keuangan",
    "kssk":     "Komite Stabilitas Sistem Keuangan",
    "bpr":      "bank perkreditan rakyat",
    "bprs":     "bank pembiayaan rakyat syariah",
    "bumn":     "badan usaha milik negara",
    "gwm":      "giro wajib minimum",
    "gwr":      "giro wajib minimum",
    "ldr":      "loan to deposit ratio",
    "car":      "capital adequacy ratio",
    "npl":      "non performing loan",
    "apbn":     "anggaran pendapatan belanja negara",
    "dndf":     "devisa hasil ekspor",
}

_ALL_ACRONYM_MAP = {**_DOC_TYPE_MAP, **_TOPIC_MAP}

_CONTENT_EXPAND_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _ALL_ACRONYM_MAP) + r")\b", re.I
)

_KIND2_PARSE_RE = re.compile(
    r"^\s*(?P<dtype>" + "|".join(re.escape(k) for k in _DOC_TYPE_MAP) + r")"
    r"(?:\s+(?P<full_name>" + "|".join(re.escape(v) for v in _DOC_TYPE_MAP.values()) + r"))?"
    r"(?:\s+(?:nomor|no\.?|nomer))?"
    r"\s+(?P<num>\d+)"
    r"(?:\s+tahun\s+(?P<year>\d{4}))?"
    r"\s*$",
    re.I,
)

_KIND2_FULL_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(v) for v in _DOC_TYPE_MAP.values()) + r")\b",
    re.I,
)

# ── v21-D: Noise words untuk weak query detection ─────────────────────────────
_QUERY_NOISE_WORDS: frozenset[str] = frozenset({
    "document", "documents", "dokumen", "information", "info",
    "regulation", "regulations", "peraturan", "aturan", "regulasi",
    "the", "it", "this", "that", "data", "itu", "ini", "tersebut",
    "about", "tentang", "regarding", "terkait", "mengenai",
    "find", "search", "cari", "open", "show", "buka", "tampilkan",
    "please", "tolong", "help", "bantu",
    # BUG #2 FIX: "repository"/"repositori" must never become an ES query.
    # When user says "open the repository", Flash sets sq='repository' and
    # kw_id=['repositori']. Without this fix, ES receives "repositori" as query
    # and returns whatever document happens to match — not what the user wants.
    "repository", "repositori", "repo",
})


def _expand_for_content_search(query: str) -> str:
    """
    For get_data / show_repository: append full name after acronym.
    "PBI 17 tahun 2025" → "PBI Peraturan Bank Indonesia 17 tahun 2025"
    """
    if not query:
        return query

    def _replace(m: re.Match) -> str:
        token    = m.group(0)
        expanded = _ALL_ACRONYM_MAP.get(token.lower(), "")
        if not expanded:
            return token
        if expanded.lower() in query.lower():
            return token
        return f"{token} {expanded}"

    return _CONTENT_EXPAND_RE.sub(_replace, query)


def _normalize_kind2(kind2: str) -> str:
    """
    For deep_search and detail: convert any acronym form to canonical ES format.
    "PBI 17 tahun 2025" → "Peraturan Bank Indonesia Nomor 17 Tahun 2025"
    """
    if not kind2:
        return kind2

    m_full = _KIND2_FULL_RE.match(kind2)
    if m_full:
        matched   = m_full.group(1)
        canonical = next(
            (v for v in _DOC_TYPE_MAP.values() if v.lower() == matched.lower()),
            matched,
        )
        rest = kind2[m_full.end():]
        return _cap_nomor_tahun(canonical + rest)

    m = _KIND2_PARSE_RE.match(kind2)
    if m:
        full = _DOC_TYPE_MAP.get(m.group("dtype").lower(), m.group("dtype"))
        num  = m.group("num")
        year = m.group("year")
        if year:
            return f"{full} Nomor {num} Tahun {year}"
        return f"{full} Nomor {num}"

    return kind2


def _cap_nomor_tahun(s: str) -> str:
    s = re.sub(r"\bnomor\b", "Nomor", s, flags=re.I)
    s = re.sub(r"\btahun\b",  "Tahun",  s, flags=re.I)
    return s


def _is_weak_query(query: str) -> bool:
    """
    v21-D / v22-H: Returns True if query has ≤ WEAK_QUERY_MIN_TOKENS meaningful tokens.
    A "meaningful token" is a word not in _QUERY_NOISE_WORDS with length > 2.
    v22-H: min length diturunkan 4→3 agar "OJK", "LPS", "UU" tidak dianggap noise.

    FIX-4: Fungsi ini hanya digunakan untuk LOGGING/DIAGNOSTIC, BUKAN untuk
    menentukan apakah fallback ke last_topic dilakukan. Fallback ke last_topic
    hanya terjadi jika sq BENAR-BENAR kosong (lihat intent_to_tool_args).
    """
    tokens = [t.lower() for t in query.split() if len(t) > 2]
    meaningful = [t for t in tokens if t not in _QUERY_NOISE_WORDS]
    return len(meaningful) <= settings.WEAK_QUERY_MIN_TOKENS


# FIX-5: _IS_DOC_ID_RE diperluas — identik dengan _IS_DOC_ID_RE_EXTENDED di classifier.py
# Menangani "PBI 17" (tanpa "nomor"), "Peraturan BI 17", format lain yang sebelumnya
# gagal dikenali dan menyebabkan ES lookup + threshold check fail yang sia-sia.
_IS_DOC_ID_RE = re.compile(
    r"\b("
    r"nomor\s+\d|"              # nomor 17
    r"no\.?\s*\d|"              # no. 17 / no 17
    r"\d+/[A-Z]+/\d{4}|"        # 17/PBI/2025
    r"tahun\s+\d{4}|"           # tahun 2025
    r"\bpbi\s*\d|"              # PBI 17 (tanpa "nomor")
    r"\bpojk\s*\d|"             # POJK 77
    r"\bpadg\s*\d|"             # PADG 3
    r"\bsebi\s*\d|"             # SEBI 5
    r"\bpp\s+\d|"               # PP 47
    r"\buu\s+\d|"               # UU 12
    r"\bperaturan\s+(bi|bank\s+indonesia)\s+\d"  # Peraturan BI 17
    r")\b",
    re.I,
)

_SPECIFIC_DOC_RE = re.compile(
    r"\b(padg|pbi|pojk|pp\s*\d|uu\s*\d|perpu|permen|pa\s+no|"
    # Full type names — agar threshold rendah (0.5) bukan 2.0
    r"peraturan\s+anggota\s+dewan|peraturan\s+bank\s+indonesia|"
    r"peraturan\s+otoritas\s+jasa|surat\s+edaran\s+bank|"
    r"peraturan\s+menteri|undang\s*-?\s*undang|peraturan\s+pemerintah)\b",
    re.I
)

# ── Newest docs cache ─────────────────────────────────────────────────────────
_newest_cache:    dict  = {}
_newest_cache_ts: float = 0.0
_newest_lock              = threading.Lock()


def _get_newest_cached() -> dict:
    global _newest_cache, _newest_cache_ts
    now = time.monotonic()
    with _newest_lock:
        if _newest_cache and (now - _newest_cache_ts) < settings.NEWEST_DOCS_CACHE_TTL:
            return _newest_cache
    fresh = get_newest_documents(
        limit       = settings.NEWEST_DOCS_LIMIT,
        months_back = settings.NEWEST_DOCS_MONTHS_BACK,
    )
    with _newest_lock:
        _newest_cache    = fresh
        _newest_cache_ts = time.monotonic()
    log.info("newest_docs refreshed: %d docs", len(fresh.get("data", [])))
    return fresh


def preload_newest_cache() -> None:
    threading.Thread(target=_get_newest_cached, daemon=True).start()


# ── Tool Declarations ─────────────────────────────────────────────────────────
TOOL_DECLARATIONS = [
    {
        "name": "get_data",
        "description": "Cari konteks peraturan relevan untuk menjawab pertanyaan pengguna.",
        "parameters": {
            "type": "object",
            "properties": {
                "query":    {"type": "string"},
                "instansi": {"type": "string"},
                "jenis":    {"type": "string"},
                "tahun":    {"type": "string"},
                "top_k":    {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "show_repository",
        "description": "Buka/tampilkan halaman repositori peraturan.",
        "parameters": {
            "type": "object",
            "properties": {
                "query":    {"type": "string"},
                "instansi": {"type": "string"},
                "jenis":    {"type": "string"},
                "tahun":    {"type": "string"},
                "top_k":    {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "detail",
        "description": "Tampilkan metadata lengkap satu peraturan.",
        "parameters": {
            "type": "object",
            "properties": {"kind2": {"type": "string"}},
            "required": ["kind2"],
        },
    },
    {
        "name": "deep_search",
        "description": "Tampilkan isi lengkap peraturan (Bab → Pasal → Ayat).",
        "parameters": {
            "type": "object",
            "properties": {"kind2": {"type": "string"}},
            "required": ["kind2"],
        },
    },
    {
        "name": "fetch_news",
        "description": "Ambil berita hukum/keuangan terbaru dari RSS feeds.",
        "parameters": {
            "type": "object",
            "properties": {
                "query":        {"type": "string"},
                "top_k":        {"type": "integer"},
                "max_age_days": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
]


# ── v21-E: Context builders ────────────────────────────────────────────────────

def _make_no_data_context(query: str) -> str:
    return (
        f"[HASIL DATABASE: TIDAK DITEMUKAN]\n"
        f"Pencarian '{query}' tidak menghasilkan dokumen di database Legalitik.\n"
        f"INSTRUKSI WAJIB: Sampaikan kepada pengguna bahwa dokumen/informasi tidak "
        f"ditemukan di database. DILARANG KERAS mengarang nomor peraturan, isi pasal, "
        f"atau informasi hukum dari pengetahuan internal. "
        f"Katakan: 'Saya tidak menemukan [topik] di database kami.'"
    )


def _make_voice_context(result: dict, query: str, open_panel: bool) -> str:
    total   = result.get("total", 0)
    context = result.get("context", "").strip()

    if total == 0:
        return _make_no_data_context(query)

    # v29 BUG-B FIX: Format lama "[PANEL BELUM DIBUKA]" membuat Gemini
    # menginterpretasikan sebagai "tidak ada data" → selalu bilang "not found".
    #
    # Root cause: string ambigu → Gemini tidak bisa membedakan antara
    #   (a) panel tidak dibuka TAPI data ada, dan
    #   (b) tidak ada data sama sekali.
    # Gemini selalu memilih interpretasi (b) = aman → "tidak ditemukan".
    #
    # Fix (Opsi 3): natural language eksplisit yang menyatakan data ADA
    # + instruksi jelas cara menyampaikan → tidak ada ruang untuk ambiguitas.
    if not open_panel:
        panel_note = (
            f"\n\nData ditemukan: {total} regulasi relevan untuk '{query}'. "
            f"Sampaikan temuan ini kepada pengguna via suara dalam 1-2 kalimat. "
            f"Sebutkan nama regulasi utama dan topiknya. "
            f"JANGAN katakan 'sudah ditampilkan di layar' atau 'sudah dibuka'."
        )
    else:
        panel_note = (
            f"\n\nRepositori sudah ditampilkan di layar dengan {total} regulasi "
            f"untuk '{query}'. Konfirmasi kepada pengguna dan sebutkan 1 regulasi utama."
        )

    anti_hallucination = (
        "\n[ANTI-HALLUCINATION: Jawab HANYA dari data di atas. "
        "DILARANG mengarang nomor atau isi peraturan.]"
    ) if settings.HALT_HALLUCINATION else ""

    return context + panel_note + anti_hallucination


# ── Tool Executor ─────────────────────────────────────────────────────────────
def execute_tool(
    tool_name:   str,
    tool_args:   dict,
    open_panel:  bool          = False,
    pre_fetched: dict | None   = None,
) -> tuple[str, dict]:
    """
    Execute a tool and return (llm_context, fe_payload).
    """
    log.info("execute_tool: %s open_panel=%s pre_fetched=%s args=%s",
             tool_name, open_panel, pre_fetched is not None, list(tool_args.keys()))

    # ── get_data: voice-only RAG ──────────────────────────────────────────
    if tool_name == "get_data":
        t0 = time.monotonic()

        if pre_fetched is not None:
            result = pre_fetched
            log.info("get_data: using pre_fetched result (saved ES roundtrip)")
        else:
            result = _get_data_voice(tool_args)

        query = tool_args.get("query", "")
        log.info("get_data: q=%r docs=%d elapsed=%.2fs",
                 query, result.get("total", 0), time.monotonic() - t0)

        # BUG #4 FIX: Previously hardcoded open_panel=False here, meaning Gemini was
        # always told "panel not open — answer by voice only" even when open_panel=True
        # and fe payload was already sending action=open_repository to the browser.
        # Fix: pass the actual open_panel value so the LLM context matches reality.
        llm_ctx = _make_voice_context(result, query, open_panel=open_panel)

        if open_panel and result.get("total", 0) > 0:
            docs = result.get("documents", [])
            if not docs:
                full_result = _get_data_full(tool_args)
                docs = full_result.get("documents", [])
            fe = {
                "action":    "open_repository",
                "query":     query,
                "documents": docs,
                "total":     result["total"],
                "newest":    _get_newest_cached().get("data", []),
            }
            return (llm_ctx, fe)

        return (llm_ctx, {})

    # ── show_repository: voice-only OR full panel ─────────────────────────
    elif tool_name == "show_repository":
        t0    = time.monotonic()
        query = tool_args.get("query", "")

        if not open_panel:
            result = _get_data_voice(tool_args)
            if result.get("total", 0) == 0:
                expanded = _expand_for_content_search(query)
                if expanded != query:
                    expanded_args = {**tool_args, "query": expanded}
                    retry_result = _get_data_voice(expanded_args)
                    if retry_result.get("total", 0) > 0:
                        log.info("show_repository: expansion retry '%s' → %d hits",
                                 expanded, retry_result["total"])
                        result = retry_result
                        query  = expanded
            log.info("show_repository (voice-only): q=%r docs=%d elapsed=%.2fs",
                     query, result.get("total", 0), time.monotonic() - t0)
            return (_make_voice_context(result, query, open_panel=False), {})

        # Full panel path
        if pre_fetched is not None and pre_fetched.get("documents"):
            result  = pre_fetched
            newest  = _get_newest_cached()
            log.info("show_repository (pre_fetched): q=%r docs=%d elapsed=%.2fs",
                     query, result["total"], time.monotonic() - t0)
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                f_data   = pool.submit(get_data, **_safe_args("get_data", tool_args))
                f_newest = pool.submit(_get_newest_cached)
                try:
                    result = f_data.result(timeout=8.0)
                except Exception as exc:
                    log.error("show_repository ES timeout/error: %s", exc)
                    result = {"documents": [], "total": 0, "context": ""}
                try:
                    newest = f_newest.result(timeout=4.0)
                except Exception:
                    newest = {"data": []}
            log.info("show_repository (full): q=%r docs=%d elapsed=%.2fs",
                     query, result["total"], time.monotonic() - t0)

        log.info("REPO query:%r docs:%d", query, result["total"])

        fe = {
            "action":    "open_repository",
            "query":     query,
            "documents": result["documents"],
            "total":     result["total"],
            "newest":    newest.get("data", []),
        }

        llm_ctx = _make_voice_context(result, query, open_panel=True)
        return (llm_ctx, fe)

    # ── detail: ALWAYS opens detail panel ────────────────────────────────
    elif tool_name == "detail":
        result = detail(**_safe_args("detail", tool_args))
        if "error" in result:
            return (result["error"], {"action": "show_error", "message": result["error"]})
        return (
            _json.dumps(result, ensure_ascii=False),
            {
                "action": "open_detail",
                "kind2":  tool_args.get("kind2", ""),
                "data":   result,
            },
        )

    # ── deep_search: ALWAYS opens doc preview panel ───────────────────────
    elif tool_name == "deep_search":
        kind2 = tool_args.get("kind2", "")
        log.info("DOC Requesting kind2:%r", kind2)
        result = deep_search(**_safe_args("deep_search", tool_args))
        if "error" in result:
            return (result["error"], {"action": "show_error", "message": result["error"]})
        return (
            f"Membuka dokumen '{result['metadata']['title']}'.",
            {
                "action":    "open_deep_search",
                "kind2":     tool_args.get("kind2"),
                "metadata":  result.get("metadata", {}),
                "structure": result.get("structure", []),
            },
        )

    # ── fetch_news: panel when open_panel=True ────────────────────────────
    elif tool_name == "fetch_news":
        from app.news_tools import fetch_legal_news
        t0     = time.monotonic()
        result = fetch_legal_news(
            query        = tool_args.get("query", ""),
            top_k        = tool_args.get("top_k",        settings.NEWS_TOP_K),
            max_age_days = tool_args.get("max_age_days", settings.NEWS_MAX_AGE_DAYS),
        )
        log.info("fetch_news: q=%r articles=%d elapsed=%.2fs",
                 tool_args.get("query"), result["total"], time.monotonic() - t0)

        if open_panel and result["total"] > 0:
            fe = {
                "action":   "show_news",
                "query":    tool_args.get("query", ""),
                "articles": result["articles"],
                "total":    result["total"],
                "source":   result.get("source", ""),
            }
        else:
            fe = {
                "_stm_articles": result["articles"],
                "total":         result["total"],
            } if result["total"] > 0 else {}
        return (result.get("context", ""), fe)

    log.warning("Unknown tool: %s", tool_name)
    return ("Tool tidak dikenal.", {})


def _get_data_voice(tool_args: dict) -> dict:
    args = _safe_args("get_data", tool_args)
    args.setdefault("top_k", settings.TOP_K_DOCS)
    return get_data(**args, voice_only=True)


def _get_data_full(tool_args: dict) -> dict:
    args = _safe_args("get_data", tool_args)
    args.setdefault("top_k", settings.TOP_K_DOCS)
    return get_data(**args, voice_only=False)


# ── Intent → tool mapping ─────────────────────────────────────────────────────
INTENT_TO_TOOL: dict[str, str] = {
    "regulation_query": "get_data",
    "open_repository":  "show_repository",
    "preview_document": "deep_search",
    "detail_document":  "detail",
    "fetch_news":       "fetch_news",
}


def intent_to_tool_args(
    intent:            str,
    classifier_result: dict,
    last_topic:        str = "",
) -> tuple[str, dict] | None:
    """
    Map classifier output → (tool_name, tool_args).

    FIX-4: Weak query NO LONGER triggers automatic last_topic override.
    sq yang "weak" (hanya 1 meaningful token, misal "GWM") tetap digunakan — ES
    yang menentukan apakah ada hasil. Override last_topic hanya jika sq BENAR-BENAR kosong.

    FIX-6: query_en derivation lebih konsisten:
    Prioritas: kw_en > sq (jika berbeda dari query_id) > None
    Ini mengurangi perbedaan antara prefetch query dan execute_tool query → kurangi cache miss.
    """
    if not isinstance(classifier_result, dict):
        log.error("intent_to_tool_args: non-dict: %r", type(classifier_result))
        return None

    tool_name = INTENT_TO_TOOL.get(intent)
    if not tool_name:
        return None

    kw_id    = " ".join(classifier_result.get("keywords_id") or []).strip()
    kw_en    = " ".join(classifier_result.get("keywords_en") or []).strip()
    sq       = (classifier_result.get("search_query") or "").strip()
    kind2    = (classifier_result.get("document_name") or "").strip()
    instansi = classifier_result.get("instansi") or None
    jenis    = classifier_result.get("jenis")    or None
    tahun    = classifier_result.get("tahun")    or None

    # ── FIX-4: Smarter empty-only fallback (NO weak-query override) ────────
    # OLD: if sq terlalu pendek (weak) → override dengan last_topic
    # NEW: HANYA override jika sq benar-benar KOSONG setelah semua upaya.
    #      Biarkan ES memutuskan apakah query "GWM" menghasilkan hits atau tidak.
    if tool_name in ("get_data", "show_repository"):
        if not sq and not kw_id and last_topic:
            # Kedua sq DAN kw_id kosong → safe to fall back
            log.info("Empty query AND kw_id → fallback to last_topic %r", last_topic)
            sq = last_topic
        elif not sq and last_topic:
            # sq kosong tapi kw_id ada → gunakan kw_id, jangan override ke last_topic
            log.info("Empty sq, kw_id=%r present → use kw_id (no last_topic override)", kw_id)
        elif sq and _is_weak_query(sq):
            # FIX-4: Log as diagnostic only — DO NOT override sq!
            log.info("Weak query detected: sq=%r (keeping as-is, not overriding with last_topic)", sq)

    # ── Acronym strategy ──────────────────────────────────────────────────
    if tool_name in ("get_data", "show_repository"):
        kw_id = _expand_for_content_search(kw_id)
        sq    = _expand_for_content_search(sq)
        kw_en = _expand_for_content_search(kw_en)
    else:
        kind2 = _normalize_kind2(kind2)

    query_id = kw_id or sq or kind2

    # BUG #2 FIX (secondary): After expansion, check if ALL tokens in query_id
    # are noise words (e.g. query_id='repositori', 'repository', 'repo').
    # If so and last_topic is available, fall back to last_topic.
    # This catches the case where the user says "open the repository, not preview"
    # and Flash correctly returns intent=open_repository but sq/kw_id = 'repositori'.
    if tool_name in ("get_data", "show_repository") and query_id:
        _useful = [t for t in query_id.split() if t.lower() not in _QUERY_NOISE_WORDS]
        if not _useful and last_topic:
            log.info(
                "query_id %r is all-noise after expansion → fallback to last_topic %r",
                query_id, last_topic
            )
            query_id = last_topic
            sq       = last_topic

    # ── FIX-6: Consistent query_en derivation ─────────────────────────────
    # Prioritas derivation: kw_en (dari Flash, paling akurat) > sq (manual)
    # Tujuan: memastikan prefetch dan execute_tool menggunakan query_en yang sama.
    query_en: str | None = None
    if kw_en and kw_en.lower() != query_id.lower():
        query_en = kw_en
    elif sq and sq.lower() != query_id.lower():
        query_en = sq

    log.info("intent_to_tool_args: %s q_id=%r q_en=%r kind2=%r last_topic=%r",
             intent, query_id, query_en, kind2, last_topic)

    if tool_name == "fetch_news":
        news_q = sq or kw_id or kw_en or "berita hukum keuangan"
        return tool_name, {"query": news_q}

    if tool_name in ("get_data", "show_repository"):
        if not query_id:
            log.warning("%s: empty query → skip", tool_name)
            return None
        args: dict[str, Any] = {"query": query_id}
        if query_en:  args["query_en"] = query_en
        if instansi:
            if instansi.lower() not in query_id.lower():
                args["query"] = f"{query_id} {instansi}"
                log.info("instansi '%s' injected into query (not using strict filter)", instansi)
            else:
                args["instansi"] = instansi
        if jenis:     args["jenis"]    = jenis
        if tahun:     args["tahun"]    = tahun
        return tool_name, args

    elif tool_name in ("detail", "deep_search"):
        resolved = kind2

        # FIX-5: _IS_DOC_ID_RE sekarang lebih luas — banyak format yang sebelumnya
        # gagal dikenali kini ter-cover. Jika sudah teridentifikasi sebagai doc ID,
        # skip ES lookup dan langsung gunakan kind2.
        if resolved and not _IS_DOC_ID_RE.search(resolved):
            log.info(
                "%s: kind2=%r looks like a topic → ES lookup (no score filter, top-1)",
                tool_name, resolved,
            )
            topic_query = resolved
            topic_en    = query_en or sq or ""
            try:
                res  = get_data(query=topic_query, query_en=topic_en or None, top_k=1)
                docs = res.get("documents", [])
            except Exception as e:
                log.error("deep_search topic→ES lookup failed: %s", e)
                docs = []

            # v29: HAPUS score threshold — ambil top-1 langsung jika ada
            if docs and isinstance(docs[0], dict):
                found_k2 = docs[0].get("kind2", "")
                if found_k2:
                    log.info("%s: topic→ES resolved kind2=%r", tool_name, found_k2)
                    resolved = found_k2

        if not resolved:
            # v28-FIX: Hapus STM fallback (last_topic) untuk deep_search/detail.
            # STM fallback menyebabkan dokumen SALAH terbuka karena last_topic
            # adalah dokumen yang terakhir dibuka, bukan yang diminta user sekarang.
            # Contoh: user minta 'Peraturan Anggota Dewan Gubernur' tapi sistem
            # membuka 'PBI 7/2025' (last_topic) — SALAH.
            # Jika query_id tersedia dari Flash, gunakan itu saja.
            # Jika benar-benar kosong, skip (return None) lebih baik dari salah.
            if not query_id:
                log.warning("%s: empty query_id → skip", tool_name)
                return None

            try:
                res  = get_data(query=query_id, query_en=query_en, top_k=3)
                docs = res.get("documents", [])
            except Exception as e:
                log.error("deep_search ES lookup failed: %s", e)
                return None

            if not docs or not isinstance(docs[0], dict):
                log.warning("%s: no ES results for q=%r", tool_name, query_id)
                return None

            # v29: HAPUS score threshold — ambil top-1 langsung tanpa filter skor
            resolved = docs[0].get("kind2", "")
            if not resolved:
                log.warning("%s: empty kind2 in top result", tool_name)
                return None
            log.info("%s: resolved kind2=%r (no score filter)", tool_name, resolved)

        return tool_name, {"kind2": resolved}

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────
_ALLOWED_ARGS: dict[str, set] = {
    "get_data":        {"query", "query_en", "instansi", "jenis", "tahun", "top_k"},
    "show_repository": {"query", "query_en", "instansi", "jenis", "tahun", "top_k"},
    "detail":          {"kind2"},
    "deep_search":     {"kind2"},
    "fetch_news":      {"query", "top_k", "max_age_days"},
}


def _safe_args(tool_name: str, args: dict) -> dict:
    allowed = _ALLOWED_ARGS.get(tool_name, set())
    return {k: v for k, v in args.items() if k in allowed and v is not None}