"""
vertex.py — Vertex AI client factory v23.0

Menyediakan satu titik pembuatan genai.Client dengan Vertex AI credentials.
Di-import oleh: main.py, classifier.py, memory.py

Usage:
    from app.vertex import make_vertex_client
    client = make_vertex_client()
"""
import logging
from functools import lru_cache

from google import genai
from google.oauth2 import service_account

from app.config import settings

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _load_credentials():
    """Load Vertex AI service account credentials dari file JSON."""
    try:
        creds = service_account.Credentials.from_service_account_file(
            settings.VERTEX_KEY_PATH,
            scopes=_SCOPES,
        )
        log.info(
            "Vertex AI credentials loaded: project=%s location=%s",
            settings.VERTEX_PROJECT,
            settings.VERTEX_LOCATION,
        )
        return creds
    except FileNotFoundError:
        log.error(
            "VERTEX_KEY_PATH tidak ditemukan: %s — pastikan file key.json ada",
            settings.VERTEX_KEY_PATH,
        )
        raise
    except Exception as exc:
        log.error("Gagal load Vertex AI credentials: %s", exc)
        raise


def make_vertex_client() -> genai.Client:
    """
    Buat genai.Client dengan Vertex AI credentials.

    Setiap pemanggilan membuat client baru (tidak di-cache) karena
    credentials mungkin di-refresh oleh Google SDK secara otomatis.
    Gunakan get_cached_client() jika butuh singleton.
    """
    creds = _load_credentials()
    return genai.Client(
        vertexai=True,
        project=settings.VERTEX_PROJECT,
        location=settings.VERTEX_LOCATION,
        credentials=creds,
    )


@lru_cache(maxsize=1)
def get_cached_client() -> genai.Client:
    """
    Singleton Vertex AI client.
    Cocok untuk Flash classifier yang dipanggil berulang.
    Untuk Live session, gunakan make_vertex_client() agar credentials fresh.
    """
    return make_vertex_client()