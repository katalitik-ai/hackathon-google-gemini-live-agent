"""
memory.py — Persistent memory system v25.0

v25.0: unlock_topic() sekarang dipanggil dari main.py (BUG-E-FIX).
All v24.0 fixes retained.

v24.0 Fixes vs v23.0:
  FIX-3 (Loophole 2 — Context Blindness / STM Reset terlalu agresif):
    reset_stm() sekarang TIDAK menghapus last_topic jika ada flag _stm_topic_locked.
    Flag ini di-set oleh update_stm() setelah RAG intent yang berhasil (ada hasil).
    Ini mencegah AI melupakan topik aktif hanya karena satu turn diklasifikasikan
    sebagai chitchat oleh Layer-0.

    Metode baru:
      - lock_topic()   : lock topic agar tidak dihapus oleh reset_stm biasa
      - unlock_topic() : unlock topic (dipanggil ketika user jelas berganti topik)
      - reset_stm_hard(): reset penuh termasuk topic lock (untuk ganti topik eksplisit)

  Semua logic v23.0 dipertahankan.
"""
import json
import logging
import re
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.prompts import LIVE_SYSTEM_PROMPT, MEMORY_CONSOLIDATE_PROMPT

log = logging.getLogger(__name__)


class MemorySystem:
    def __init__(self, memory_file: str | None = None) -> None:
        self._file          = Path(memory_file or settings.MEMORY_FILE)
        self._short_term    = deque(maxlen=settings.MEMORY_MAX_SHORT_TERM)
        self._session_turns: list = []
        self._long_term: dict     = self._blank_long_term()
        self._lock                = threading.Lock()
        self._last_intent: str    = ""
        self._last_topic:  str    = ""
        self._last_results: list  = []
        self._nav_just_happened: bool = False
        # FIX-3: Topic lock — mencegah reset_stm dari menghapus topic saat berhasil RAG
        self._stm_topic_locked: bool = False
        self._load()

    @staticmethod
    def _blank_long_term() -> dict:
        return {"facts": [], "preferences": [], "summaries": [], "user_name": "",
                "legal_names": [], "created": datetime.now().isoformat(),
                "last_updated": None, "session_count": 0}

    def _load(self) -> None:
        try:
            with open(self._file, encoding="utf-8") as fh:
                data = json.load(fh)
            for key in self._blank_long_term():
                if key in data:
                    self._long_term[key] = data[key]
            s = self.stats
            log.info("Memory loaded — facts:%d user:'%s' sessions:%d",
                     s["facts"], s["user_name"], s["sessions"])
        except FileNotFoundError:
            log.info("No memory file — starting fresh")
            self._save()
        except Exception as exc:
            log.warning("Memory load error: %s — resetting", exc)
            self._long_term = self._blank_long_term()
            self._save()

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._long_term["last_updated"] = datetime.now().isoformat()
            with open(self._file, "w", encoding="utf-8") as fh:
                json.dump(self._long_term, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            log.error("Memory save error: %s", exc)

    _NAME_NOISE = frozenset({
        "but","and","or","the","a","an","is","are","was","were","be","been",
        "not","so","if","when","yes","no","ok","okay","well","just","also",
        "me","my","i","you","he","she","it","we","they","this","that","here",
        "there","then","now","stop","please","thank","thanks",
        "tapi","dan","atau","ya","iya","oke","saya","kamu",
    })

    _NAME_PATTERNS = [
        (5, re.compile(r"call me\s+(\w+)",      re.I)),
        (5, re.compile(r"panggil saya\s+(\w+)", re.I)),
        (3, re.compile(r"my name is\s+(\w+)",   re.I)),
        (3, re.compile(r"nama saya\s+(\w+)",    re.I)),
        (1, re.compile(r"i am\s+(\w+)",         re.I)),
    ]

    def add_turn(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        entry = {"role": role, "text": text, "ts": datetime.now().isoformat()}
        with self._lock:
            self._short_term.append(entry)
            self._session_turns.append(entry)
            if len(self._session_turns) > 50:
                self._session_turns = self._session_turns[-50:]
        if role == "user":
            self._try_capture_name(text)

    def _try_capture_name(self, text: str) -> None:
        best_name = ""; best_priority = -1
        for priority, pattern in self._NAME_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip().title()
                if (len(candidate) >= 2 and candidate.lower() not in self._NAME_NOISE
                        and priority > best_priority):
                    best_name = candidate; best_priority = priority
        if best_name:
            with self._lock:
                if self._long_term["user_name"] != best_name:
                    self._long_term["user_name"] = best_name
                    self._save()

    def update_stm(self, intent: str, topic: str, *, results: list | None = None) -> None:
        topic = (topic or "").strip(); intent = (intent or "").strip()
        if intent: self._last_intent = intent
        if topic:  self._last_topic  = topic
        if results is not None:
            self._last_results = results[:10]
        _NAV_ONLY = {"chitchat", "close_panel", "back_to_repository", ""}
        if intent and intent not in _NAV_ONLY:
            self._nav_just_happened = False
            # FIX-3: Lock topic jika RAG intent berhasil (ada results atau topic tidak kosong)
            _RAG_LOCK_INTENTS = {"open_repository", "regulation_query", "preview_document", "fetch_news"}
            if intent in _RAG_LOCK_INTENTS and topic:
                self._stm_topic_locked = True
                log.debug("STM topic locked: intent=%s topic=%r", intent, topic)

    def mark_nav_action(self) -> None:
        self._nav_just_happened = True

    @property
    def nav_just_happened(self) -> bool:
        return self._nav_just_happened

    def lock_topic(self) -> None:
        """FIX-3: Explicitly lock the current topic."""
        self._stm_topic_locked = True

    def unlock_topic(self) -> None:
        """FIX-3: Explicitly unlock the topic (user clearly starting new topic)."""
        self._stm_topic_locked = False

    def reset_stm(self) -> None:
        """
        FIX-3: Soft reset — clears intent/nav state but PRESERVES topic if locked.
        Dipanggil oleh: chitchat after RAG, context refresh, dll.
        Topic tetap tersimpan jika user masih dalam konteks topik yang sama.
        """
        self._last_intent = ""
        self._last_results = []
        self._nav_just_happened = False
        # FIX-3: Hanya hapus topic jika TIDAK locked
        if not self._stm_topic_locked:
            self._last_topic = ""
            log.debug("STM reset: intent+topic cleared (not locked)")
        else:
            log.debug("STM reset: intent cleared, topic PRESERVED (locked=%r)", self._last_topic)

    def reset_stm_hard(self) -> None:
        """
        FIX-3: Hard reset — clears everything including locked topic.
        Dipanggil saat user JELAS berganti topik baru (misalnya explicit new query
        yang sangat berbeda dari last_topic).
        """
        self._last_intent = ""
        self._last_topic  = ""
        self._last_results = []
        self._nav_just_happened = False
        self._stm_topic_locked = False
        log.debug("STM hard reset: all cleared")

    @property
    def last_intent(self) -> str: return self._last_intent
    @property
    def last_topic(self)  -> str: return self._last_topic
    @property
    def last_results(self) -> list: return self._last_results
    @property
    def stm_topic_locked(self) -> bool: return self._stm_topic_locked

    def recent_turns_for_classifier(self, n: int = 4) -> str:
        with self._lock:
            turns = list(self._short_term)[-n:]
        lines = []
        for t in turns:
            role = "USER" if t["role"] == "user" else "ASSISTANT"
            lines.append(f"{role}: {t['text'][:120]}")
        if self._last_intent:
            lines.append(f'[LAST ACTION: intent={self._last_intent}, topic="{self._last_topic}"]')
        if self._last_results:
            result_lines = []
            items = self._last_results
            for idx, item in enumerate(items[:5], start=1):
                if isinstance(item, dict):
                    t = item.get("title") or item.get("kind2") or item.get("name") or ""
                    if t: result_lines.append(f"  [{idx}] {t}")
            total = len(items)
            if total > 5:
                last_item = items[-1]
                if isinstance(last_item, dict):
                    t = last_item.get("title") or last_item.get("kind2") or last_item.get("name") or ""
                    if t: result_lines.append(f"  [{total}] (terakhir) {t}")
            if result_lines:
                lines.append(f"[LAST RESULTS — {total} item(s)]:\n" + "\n".join(result_lines))
        return "\n".join(lines) if lines else "(no prior conversation)"

    def recent_turns_for_refresh(self, n: int = 6) -> str:
        with self._lock:
            turns = list(self._short_term)[-n:]
        if not turns:
            return "(new session)"
        lines = []
        for t in turns:
            role = "User" if t["role"] == "user" else "Assistant"
            lines.append(f"{role}: {t['text'][:150]}")
        return "\n".join(lines)

    def build_system_prompt(self) -> str:
        lt = self._long_term; ctx_parts: list[str] = []
        if lt.get("user_name"):
            ctx_parts.append(f"User's preferred name: {lt['user_name']}")
        if lt["facts"]:
            ctx_parts.append("Known facts: " + " | ".join(lt["facts"][-8:]))
        if lt["preferences"]:
            ctx_parts.append("Preferences: " + " | ".join(lt["preferences"][-4:]))
        if lt.get("legal_names"):
            ctx_parts.append("Recently discussed regulations: " + ", ".join(lt["legal_names"][-5:]))
        if lt["summaries"]:
            ctx_parts.append(f"Previous session summary: {lt['summaries'][-1]}")
        if self._short_term:
            recent = "\n".join(f"{t['role'].upper()}: {t['text']}"
                               for t in list(self._short_term)[-8:])
            ctx_parts.append(f"Recent conversation:\n{recent}")
        context_block = "\n".join(ctx_parts) if ctx_parts else "(no memory yet)"
        return LIVE_SYSTEM_PROMPT.format(memory_context=context_block)

    def consolidate(self, api_key: str = "", client=None) -> None:
        """
        v23/v24: Uses Vertex AI client.
        """
        with self._lock:
            self._long_term["session_count"] += 1
            session_turns       = list(self._session_turns)
            self._session_turns = []
        self.reset_stm_hard()
        if not session_turns:
            self._save(); return
        log.info("Consolidating %d turns...", len(session_turns))
        extracted: dict = {}
        try:
            if client is None:
                from app.vertex import make_vertex_client
                client = make_vertex_client()
            conversation = "\n".join(f"{t['role'].upper()}: {t['text']}"
                                     for t in session_turns)
            prompt = MEMORY_CONSOLIDATE_PROMPT.format(conversation=conversation)
            from google.genai import types as gtypes
            response = client.models.generate_content(
                model=settings.FLASH_MODEL,
                contents=prompt,
                config=gtypes.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=512,
                    thinking_config=gtypes.ThinkingConfig(include_thoughts=False),
                ),
            )
            raw_text = (response.text or "").strip().replace("```json","").replace("```","").strip()
            extracted = json.loads(raw_text)
            log.info("Consolidation succeeded")
        except Exception as exc:
            log.error("Consolidation error: %s", exc)

        with self._lock:
            for fact in extracted.get("facts", []):
                if fact and fact not in self._long_term["facts"]:
                    self._long_term["facts"].append(fact)
            for pref in extracted.get("preferences", []):
                if pref and pref not in self._long_term["preferences"]:
                    self._long_term["preferences"].append(pref)
            summary = (extracted.get("summary") or "").strip()
            if summary:
                self._long_term["summaries"].append(summary)
            captured_name = (extracted.get("user_name") or "").strip()
            if captured_name and not self._long_term["user_name"]:
                self._long_term["user_name"] = captured_name
            for legal_name in extracted.get("legal_names", []):
                if legal_name and legal_name not in self._long_term["legal_names"]:
                    self._long_term["legal_names"].append(legal_name)
            self._long_term["facts"]       = self._long_term["facts"][-settings.MEMORY_MAX_FACTS:]
            self._long_term["preferences"] = self._long_term["preferences"][-settings.MEMORY_MAX_PREFS:]
            self._long_term["summaries"]   = self._long_term["summaries"][-settings.MEMORY_MAX_SUMMARIES:]
            self._long_term["legal_names"] = self._long_term["legal_names"][-settings.MEMORY_MAX_LEGAL:]
            self._save()
        log.info("Consolidation complete")

    def clear(self) -> None:
        with self._lock:
            self._long_term = self._blank_long_term()
            self._short_term.clear()
            self._session_turns = []
            self._save()
        self.reset_stm_hard()
        log.info("Memory cleared")

    @property
    def stats(self) -> dict:
        return {"facts": len(self._long_term["facts"]),
                "preferences": len(self._long_term["preferences"]),
                "summaries": len(self._long_term["summaries"]),
                "short": len(self._short_term),
                "sessions": self._long_term["session_count"],
                "user_name": self._long_term.get("user_name", ""),
                "legal_names": len(self._long_term.get("legal_names", [])),
                "topic_locked": self._stm_topic_locked}

    @property
    def snapshot(self) -> dict:
        return {"stats": self.stats, "long_term": self._long_term,
                "short_term": list(self._short_term)}