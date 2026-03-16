"""
config.py — Legalitik Voice Agent v23.0

Changes vs v22.5:
  v23-VERTEX: Migrasi ke Vertex AI
    - VERTEX_KEY_PATH / VERTEX_PROJECT / VERTEX_LOCATION
    - LIVE_MODEL  → "gemini-live-2.5-flash-native-audio"
    - FLASH_MODEL → "gemini-2.5-flash-lite" (compatible Vertex & non-Vertex)

  v23-LANG: Language default English
    - AI menjawab Inggris secara default.
    - Beralih ke Indo hanya jika user bicara Indo.

  v23-FIX-A: SKIP_FINAL_IF_EARLY_EXECUTED
    - Cegah double execute_tool (open_deep_search dipanggil dua kali).

  Semua VAD settings v22.2 dipertahankan.
"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


def _default_memory_file() -> str:
    prod_dir = "/home/data/prod/voice_agent/memory"
    if Path(prod_dir).is_dir() and os.access(prod_dir, os.W_OK):
        return f"{prod_dir}/agent_memory.json"
    return str(Path(__file__).resolve().parent.parent / "agent_memory.json")


class Settings(BaseSettings):
    # ── Elasticsearch ──────────────────────────────────────────────────────
    ES_HOST:         str   = "http://localhost:9200/"
    ES_USER:         str   = "user"
    ES_PASS:         str   = "pass"
    ES_INDEX:        str   = "index-1"
    ES_INDEX_SEARCH: str   = "index-2"
    ES_TIMEOUT:      int   = 5
    ES_MAX_RETRIES:  int   = 1
    TOP_K_CHUNKS:    int   = 5
    TOP_K_DOCS:      int   = 5

    # ── Vertex AI (v23) ────────────────────────────────────────────────────
    VERTEX_KEY_PATH: str   = "key.json"
    VERTEX_PROJECT:  str   = "project-id"
    VERTEX_LOCATION: str   = "location"

    # ── Gemini model names ─────────────────────────────────────────────────
    LIVE_MODEL:      str   = "gemini-live-2.5-flash-native-audio"
    FLASH_MODEL:     str   = "gemini-2.5-flash-lite"
    GEMINI_VOICE:    str   = "Puck"

    # ── Flash classifier ───────────────────────────────────────────────────
    FLASH_MAX_RETRIES:         int   = 2
    FLASH_RETRY_DELAY:         float = 0.15
    EARLY_FLASH_ONE_PER_TURN:  bool  = True
    EARLY_CLASSIFICATION_MIN_FRAGS: int  = 2
    EARLY_CLASSIFICATION_MIN_CHARS: int  = 5
    SEMAPHORE_CLASSIFY:        bool  = False  # obsolete, kept for compat
    ANAPHORIC_FLASH:           bool  = True
    CONSOLIDATE_MIN_TURNS:     int   = 3
    STM_FOLLOWUP_MAX_WORDS:    int   = 6
    WEAK_QUERY_MIN_TOKENS:     int   = 1
    HALT_HALLUCINATION:        bool  = True

    # ── VAD (v22.2 values) ─────────────────────────────────────────────────
    VAD_SILENCE_DURATION_MS:    int  = 1600
    VAD_END_SENSITIVITY_HIGH:   bool = False
    VAD_START_SENSITIVITY_LOW:  bool = True

    # ── STM / context ─────────────────────────────────────────────────────
    STM_CHITCHAT_CLEAR:               bool  = True
    NEWS_VOICE_CONTEXT_MAX_SENTENCES: int   = 2
    CHITCHAT_AFTER_RAG_CONTEXT_INJECT: bool = True

    # ── News ───────────────────────────────────────────────────────────────
    NEWS_TOP_K:             int   = 5
    NEWS_MAX_AGE_DAYS:      int   = 30
    NEWS_CONTEXT_MAX_CHARS: int   = 1500
    FETCH_NEWS_VOICE_ONLY:  bool  = True

    # ── Server ─────────────────────────────────────────────────────────────
    PORT:        int  = 8000
    MEMORY_FILE: str  = _default_memory_file()

    # ── ngrok ──────────────────────────────────────────────────────────────
    NGROK_AUTH_TOKEN: str  = "token-ngrok"
    USE_NGROK:        bool = True

    # ── RAG ────────────────────────────────────────────────────────────────
    NEWEST_DOCS_LIMIT:       int   = 20
    NEWEST_DOCS_MONTHS_BACK: int   = 3
    RAG_CONTEXT_MAX_CHARS:   int   = 1200
    NEWEST_DOCS_CACHE_TTL:   float = 300.0
    # v29: score threshold dihapus — ES selalu return top-5, tidak ada reject by score
    AMBIGUOUS_DOC_MIN_SCORE: float = 0.0   # deprecated, set 0 = terima semua
    PADG_PA_SCORE_THRESHOLD: float = 0.0   # deprecated, set 0 = terima semua

    # ── Memory ─────────────────────────────────────────────────────────────
    MEMORY_MAX_FACTS:      int = 60
    MEMORY_MAX_PREFS:      int = 25
    MEMORY_MAX_SUMMARIES:  int = 12
    MEMORY_MAX_LEGAL:      int = 50
    MEMORY_MAX_SHORT_TERM: int = 20

    # ── Classifier ─────────────────────────────────────────────────────────
    LAYER1_SKIP_FLASH_NAV: bool = True
    PREFETCH_ES:           bool = True
    STM_TOPIC_FALLBACK:    bool = True

    # ── Context refresh ────────────────────────────────────────────────────
    CONTEXT_REFRESH_EVERY_N_TURNS: int = 5

    # ── Cancel words ───────────────────────────────────────────────────────
    CANCEL_WORDS: list = [
        "stop", "cancel", "interrupt", "never mind", "nevermind", "wait",
        "hold on", "pause",
        "berhenti", "batalkan", "diam", "tunggu", "tahan",
        "cukup", "oke stop", "ok stop",
    ]

    # ── Fast-path chitchat ────────────────────────────────────────────────
    FAST_CHITCHAT_SKIP_FLASH: bool = True

    # ── Panel opening policy ──────────────────────────────────────────────
    VOICE_ONLY_RAG: bool = True

    # ── v23: Double-execute prevention ────────────────────────────────────
    SKIP_FINAL_IF_EARLY_EXECUTED: bool = True

    class Config:
        env_file          = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()