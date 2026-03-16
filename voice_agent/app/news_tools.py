"""
news_tools.py — Legal news retrieval v18.1 (RSS-only, no API key needed)

Feed list diperbaiki berdasarkan log error:
  CNBC Indonesia /rss/rssindex.aspx?type=finance → 404 (diganti ke /market/rss)
  Kontan SSL handshake failure → diganti ke Katadata & Tempo Bisnis
  Bisnis.com /kategori/finansial.rss → 404 (diganti ke /ekonomi-bisnis)

Feeds aktif:
  1. Detik Finance           — finance.detik.com (terbukti OK di log)
  2. CNBC Indonesia Market   — cnbcindonesia.com/market
  3. Katadata                — katadata.co.id
  4. Tempo Bisnis            — bisnis.tempo.co
  5. OJK Siaran Pers         — ojk.go.id (terbukti OK di log)

Public API:
  fetch_legal_news(query, top_k, max_age_days) → dict
"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.request import urlopen, Request
import ssl

from app.config import settings

log = logging.getLogger(__name__)

# ── RSS feed list (updated — berdasarkan log v17.0 yang error) ────────────────
_RSS_FEEDS = [
    ("Detik Finance",
     "https://finance.detik.com/moneter/rss"),
    ("CNBC Indonesia",
     "https://www.cnbcindonesia.com/market/rss"),
    ("Katadata",
     "https://katadata.co.id/rss"),
    ("Tempo Bisnis",
     "https://bisnis.tempo.co/rss"),
    ("OJK Siaran Pers",
     "https://www.ojk.go.id/id/rss"),
]

_HTTP_TIMEOUT = 4  # detik per feed — cukup; fail fast untuk feed lambat


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _http_get(url: str) -> bytes | None:
    """Fetch URL dengan timeout. Coba tanpa SSL verify jika SSL error."""
    headers = {"User-Agent": "LegalitikVoiceAgent/17 (+https://legalitik.id)"}
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.read()
    except ssl.SSLError:
        # Fallback: skip SSL verify (untuk feed dengan cert bermasalah)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
            req = Request(url, headers=headers)
            with urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
                return resp.read()
        except Exception as exc:
            log.warning("RSS SSL fallback failed %s: %s", url, exc)
            return None
    except Exception as exc:
        log.warning("RSS fetch failed %s: %s", url, exc)
        return None


# ── Date helpers ──────────────────────────────────────────────────────────────

def _parse_date(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _age_ok(pub_date: Optional[datetime], max_age_days: int) -> bool:
    if pub_date is None:
        return True  # tanggal tidak diketahui → sertakan saja
    return (datetime.utcnow() - pub_date) <= timedelta(days=max_age_days)


# ── RSS parser ────────────────────────────────────────────────────────────────

def _tag(block: str, tag: str) -> str:
    m = re.search(
        rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
        block, re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def _parse_rss(xml_bytes: bytes, keywords: list[str], max_age_days: int) -> list[dict]:
    xml   = xml_bytes.decode("utf-8", errors="replace")
    items = re.findall(r"<item[^>]*>(.*?)</item>", xml, re.DOTALL)

    results = []
    for item in items:
        title   = re.sub(r"<[^>]+>", "", _tag(item, "title")).strip()
        desc    = re.sub(r"<[^>]+>", "", _tag(item, "description")).strip()
        link    = _tag(item, "link") or _tag(item, "guid")
        pub_raw = _tag(item, "pubDate") or _tag(item, "dc:date")
        pub_dt  = _parse_date(pub_raw)

        if not _age_ok(pub_dt, max_age_days):
            continue

        combined = (title + " " + desc).lower()
        if keywords and not any(kw in combined for kw in keywords):
            continue

        results.append({
            "title":     title,
            "url":       link,
            "snippet":   desc[:220],
            "source":    "",
            "published": pub_raw[:16] if pub_raw else "",
        })

    return results


# ── Core fetch ────────────────────────────────────────────────────────────────

def _fetch_rss(query: str, top_k: int, max_age_days: int) -> list[dict]:
    _STOP = {
        "dan","di","ke","dari","yang","untuk","dengan","atau","oleh","pada",
        "the","and","or","of","in","on","at","a","an",
        "berita","news","terbaru","latest","update","tentang","about","find","me",
    }
    keywords = [w for w in query.lower().split()
                if len(w) > 2 and w not in _STOP]

    all_articles: list[dict] = []

    for feed_name, feed_url in _RSS_FEEDS:
        if len(all_articles) >= top_k * 2:
            break
        raw = _http_get(feed_url)
        if not raw:
            continue
        items = _parse_rss(raw, keywords, max_age_days)
        for item in items:
            item["source"] = feed_name
            all_articles.append(item)

    log.info("RSS: q=%r → %d raw articles", query, len(all_articles))

    # Deduplicate by title
    seen:   set[str]   = set()
    unique: list[dict] = []
    for art in all_articles:
        key = art["title"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(art)

    return unique[:top_k]


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(articles: list[dict], query: str, max_chars: int) -> str:
    """
    v22: Format output dengan newline konsisten per item berita.
    main.py mem-parse berdasarkan baris yang diawali digit + titik (regex: ^\\d+\\.).
    Setiap item sekarang dalam satu baris: "1. Judul (Sumber) [tanggal]"
    tanpa multi-line snippet untuk memudahkan truncation di context injection.
    """
    if not articles:
        return f"[Tidak ada berita terbaru untuk topik '{query}'.]"

    parts = [f"[BERITA HUKUM/KEUANGAN TERBARU — topik: '{query}']"]
    for i, art in enumerate(articles, 1):
        title = art.get('title', '-')
        meta_parts = []
        if art.get("source"):
            meta_parts.append(art["source"])
        if art.get("published"):
            meta_parts.append(art["published"][:10])
        meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
        # v22: satu baris per item — mudah di-parse regex r"^\d+\." di main.py
        line = f"{i}. {title}{meta}"
        # Tambahkan snippet singkat setelah titik dua jika ada
        if art.get("snippet"):
            snippet = art["snippet"][:100].replace("\n", " ")
            line += f" — {snippet}"
        parts.append(line)

    return "\n".join(parts)[:max_chars]


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_legal_news(
    query:        str,
    top_k:        int = settings.NEWS_TOP_K,
    max_age_days: int = settings.NEWS_MAX_AGE_DAYS,
) -> dict:
    """
    Ambil berita hukum/keuangan terbaru via RSS (tanpa API key).

    Returns:
        articles   : list[{title, url, snippet, source, published}]
        context    : str — teks untuk diinject ke Gemini Live
        total      : int
        source     : "rss"
        query_used : str
    """
    t0         = time.monotonic()
    news_query = _enrich_query(query)
    articles   = _fetch_rss(news_query, top_k, max_age_days)
    elapsed    = time.monotonic() - t0

    log.info("fetch_legal_news: q=%r → %d articles in %.2fs", query, len(articles), elapsed)

    return {
        "articles":   articles,
        "context":    _build_context(articles, query, settings.NEWS_CONTEXT_MAX_CHARS),
        "total":      len(articles),
        "source":     "rss",
        "query_used": news_query,
    }


def _enrich_query(raw: str) -> str:
    q          = raw.strip()
    news_words = {"berita","news","terbaru","latest","update",
                  "regulasi","peraturan","keuangan","finansial"}
    if any(w in q.lower() for w in news_words):
        return q
    return f"{q} keuangan regulasi"