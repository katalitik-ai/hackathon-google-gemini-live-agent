"""
main.py — Legalitik Voice Agent v29.0

Changes vs v24.0 (bug fixes dari deep analysis — all fixes now complete):

  BUG-A-FIX: asyncio.CancelledError di execute_tool sekarang memanggil
    _close_placeholder() agar Gemini tidak stuck saat user interrupt ES query.

  BUG-B-FIX: Jika llm_context kosong setelah execute_tool (edge case),
    _close_placeholder() tetap dipanggil — placeholder tidak dibiarkan terbuka.

  BUG-C-FIX: _close_placeholder() sekarang menggunakan " " (whitespace-only)
    persis seperti _HOLD_GATE, bukan string natural Indonesia yang bisa
    diucapkan oleh Gemini TTS. Reason string hanya untuk logging.

  BUG-E-FIX: topic_lock sekarang di-unlock otomatis ketika Flash classify
    intent RAG baru dengan topik yang tidak ada token overlap dengan last_topic.
    Sebelumnya topic_lock tidak pernah di-unlock selama session normal.

Changes v24.0 dipertahankan:
  FIX-1 (Loophole 1 — Triage Paralysis):
    _HOLD_GATE timeout guard ditambahkan. Jika execute_tool gagal/timeout dan
    placeholder turn masih terbuka (turn_complete=False), sistem kini WAJIB menutup
    turn dengan fallback message agar Gemini tidak stuck dalam waiting state selamanya.
    Sebelumnya beberapa exception path di _process_turn tidak menutup placeholder turn.
    Sekarang ada satu titik penutupan terpusat: _close_placeholder_if_needed().

  FIX-3 (Loophole 2 — Context Blindness):
    reset_stm() di MemorySystem sekarang TIDAK menghapus last_topic jika topic_locked.
    Di main.py: chitchat after RAG hanya memanggil memory.reset_stm() (soft),
    BUKAN reset_stm_hard(). Hard reset hanya terjadi ketika user explicitly memulai
    topik baru yang tidak berkaitan (deteksi via topic_change_detected()).

  FIX-7 (Loophole 6 — Race Condition Fragment Processing):
    Placeholder turn yang terbuka (turn_complete=False) sekarang dilacak dengan
    _placeholder_gen. Jika gen berubah (task dibatalkan karena ada turn baru dari user),
    placeholder lama tidak akan di-close oleh task baru — mencegah double close.
    Setiap session.send() sekarang dibungkus dalam try/except yang lebih granular
    agar partial failure tidak menyebabkan task seluruhnya gagal.

  Semua fix v23.0 dipertahankan.
"""

import asyncio
import base64
import json
import logging
import logging.config
import os
import threading
import time
import re
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from google.genai import types

from app.classifier import (
    FlashClassifier, needs_flash, has_anaphoric, has_stm_followup,
    is_explicit_open, fast_classify, UI_OPEN_INTENTS, _asr_normalize,
)
from app.config import settings
from app.function_calling import execute_tool, intent_to_tool_args, preload_newest_cache
from app.memory import MemorySystem
from app.vertex import make_vertex_client

# ── Logging ───────────────────────────────────────────────────────────────────
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    },
    "root": {"level": "INFO", "handlers": ["console"]},
})
log = logging.getLogger("legalitik.main")

# ── Global singletons ─────────────────────────────────────────────────────────
# v23: FlashClassifier menggunakan shared Vertex AI client
_vertex_client      = make_vertex_client()
memory              = MemorySystem()
classifier          = FlashClassifier(client=_vertex_client)
_consolidation_done = True

_CANCEL_SET: frozenset[str] = frozenset(
    w.lower().strip() for w in settings.CANCEL_WORDS
)

_NAV_FLASH_SKIP = {"back_to_repository", "close_panel", "highlight_keywords"}
_ALWAYS_PANEL   = {"preview_document", "detail_document"}

# Helper: apakah document_name sudah lengkap untuk langsung execute?
_COMPLETE_DOC_RE = re.compile(
    r"(peraturan\s+\w[\w\s]+|padg|pbi|pojk|pp|uu|perpu|permen)"
    r".*?nomor\s+\d+.*?tahun\s+\d{4}",
    re.I,
)

# v21-D: Meaningful token checker untuk early execute guard
_MEANINGFUL_TOKEN_RE = re.compile(r"\b[a-zA-Z\u00C0-\u024F]{4,}\b")
_QUERY_NOISE = frozenset({
    "document", "documents", "dokumen", "information", "info",
    "regulation", "regulations", "peraturan", "aturan", "regulasi",
    "find", "search", "cari", "open", "show", "buka", "tampilkan",
    "please", "tolong", "help", "bantu", "the", "about",
})


def _count_meaningful_tokens(text: str) -> int:
    tokens = _MEANINGFUL_TOKEN_RE.findall(text.lower())
    return sum(1 for t in tokens if t not in _QUERY_NOISE)


def _detect_lang(text: str) -> str:
    """
    Detect whether the user's utterance is English ('en') or Indonesian ('id').
    Strategy: if > 75% of alphabetic characters are plain ASCII → English.
    This is a fast heuristic — no NLP library needed.
    Used so context injection messages append the correct language instruction,
    preventing Gemini from mirroring the Indonesian context and replying in ID
    when the user is speaking EN.
    """
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return "id"
    ascii_ratio = sum(1 for c in alpha if ord(c) < 128) / len(alpha)
    return "en" if ascii_ratio > 0.75 else "id"


_HTML_PATH = Path(__file__).parent / "frontend.html"
HTML = _HTML_PATH.read_text(encoding="utf-8") if _HTML_PATH.exists() else "<h1>frontend.html not found</h1>"


# ══════════════════════════════════════════════════════════════════════════════
#  FastAPI app
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI(
    title    = "Legalitik Voice Agent",
    version  = "29.0",
    docs_url = "/api/docs",
    redoc_url= None,
)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.get("/memory")
async def get_memory():
    return JSONResponse(memory.snapshot)


@app.post("/memory/clear")
async def clear_memory():
    memory.clear()
    return JSONResponse({"ok": True, "snapshot": memory.snapshot})


@app.get("/memory/status")
async def memory_status():
    return JSONResponse({"done": _consolidation_done})


@app.get("/health")
async def health():
    return {"status": "ok", "model": settings.LIVE_MODEL, "version": "29.0"}


@app.get("/documents/newest")
async def get_newest(
    limit:       int = settings.NEWEST_DOCS_LIMIT,
    months_back: int = settings.NEWEST_DOCS_MONTHS_BACK,
    regulator:   str = "",
):
    from app.rag_tools import get_newest_documents
    result = await asyncio.get_event_loop().run_in_executor(
        None, get_newest_documents, limit, months_back, regulator or None
    )
    return JSONResponse(result)


# ══════════════════════════════════════════════════════════════════════════════
#  WebSocket endpoint
# ══════════════════════════════════════════════════════════════════════════════
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    log.info("Browser connected")

    async def send_json(payload: dict):
        try:
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    async def log_ws(level: str, tag: str, msg: str):
        log.info("[%s] %s", tag, msg)
        await send_json({"type": "log", "level": level, "tag": tag, "data": msg})

    async def send_memory_stats():
        snap = memory.snapshot
        await send_json({
            "type":       "memory_stats",
            "stats":      snap["stats"],
            "long_term":  snap["long_term"],
            "short_term": snap["short_term"],
        })

    # ── Bootstrap ─────────────────────────────────────────────────────────
    sys_prompt = memory.build_system_prompt()
    s          = memory.stats
    await log_ws("info", "SERVER",
                 f"Connecting → {settings.LIVE_MODEL} [v25.0 | Vertex AI]")
    await log_ws("memory", "MEM",
                 f"Context: facts:{s['facts']} prefs:{s['preferences']} "
                 f"user:'{s['user_name']}' sessions:{s['sessions']}")
    await send_memory_stats()

    # v23: use shared Vertex AI client (no api_key needed)
    client = _vertex_client

    live_cfg_kwargs: dict = dict(
        response_modalities  = ["AUDIO"],
        system_instruction   = types.Content(parts=[types.Part(text=sys_prompt)]),
        speech_config        = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=settings.GEMINI_VOICE
                )
            )
        ),
    )

    # v22-A / v22.1-C / v22.2: VAD multi-tier fallback
    # ──────────────────────────────────────────────────────────────────────
    # v22.2 KOREKSI SENSITIVITY:
    #   end_of_speech_sensitivity: HIGH → LOW
    #     HIGH = potong turn di jeda microsecond → user selalu terpotong di tengah kalimat
    #     LOW  = butuh silence lebih lama sebelum cut → toleran jeda natural antar kata
    #   silence_duration_ms: 800 → 1600ms
    #     800ms terlalu pendek untuk natural conversation (jeda normal 200-500ms per kata)
    #     1600ms = cukup buffer untuk kalimat panjang tanpa terlalu lambat
    #   Tier-2 sekarang mencoba silence_duration_ms (Tier-2A) sebelum tanpa (Tier-2B)
    # ──────────────────────────────────────────────────────────────────────
    _vad_applied = False

    # Baca sensitivity enums dari config (bisa dioverride via env var)
    try:
        _end_sens = (types.EndSensitivity.END_SENSITIVITY_HIGH
                     if settings.VAD_END_SENSITIVITY_HIGH
                     else types.EndSensitivity.END_SENSITIVITY_LOW)
        _start_sens = (types.StartSensitivity.START_SENSITIVITY_LOW
                       if settings.VAD_START_SENSITIVITY_LOW
                       else types.StartSensitivity.START_SENSITIVITY_HIGH)
    except AttributeError:
        _end_sens   = None
        _start_sens = None

    _silence_ms = settings.VAD_SILENCE_DURATION_MS

    # Tier 1 — VoiceActivityDetection (SDK ≥ 1.10)
    if not _vad_applied and _end_sens is not None:
        try:
            live_cfg_kwargs["realtime_input_config"] = types.RealtimeInputConfig(
                voice_activity_detection=types.VoiceActivityDetection(
                    disabled                    = False,
                    start_of_speech_sensitivity = _start_sens,
                    end_of_speech_sensitivity   = _end_sens,
                    silence_duration_ms         = _silence_ms,
                )
            )
            _vad_applied = True
            await log_ws("ok", "VAD",
                         f"Tier-1 VoiceActivityDetection: silence={_silence_ms}ms end=LOW")
        except (AttributeError, TypeError, Exception):
            pass

    # Tier 2A — AutomaticActivityDetection + silence_duration_ms + sensitivity
    if not _vad_applied and _end_sens is not None:
        try:
            live_cfg_kwargs["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled                    = False,
                    start_of_speech_sensitivity = _start_sens,
                    end_of_speech_sensitivity   = _end_sens,
                    silence_duration_ms         = _silence_ms,
                )
            )
            _vad_applied = True
            await log_ws("ok", "VAD",
                         f"Tier-2A AutomaticActivityDetection+silence: {_silence_ms}ms end=LOW")
        except (AttributeError, TypeError, Exception):
            pass

    # Tier 2B — AutomaticActivityDetection + sensitivity saja (tanpa silence_duration_ms)
    if not _vad_applied and _end_sens is not None:
        try:
            live_cfg_kwargs["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled                    = False,
                    start_of_speech_sensitivity = _start_sens,
                    end_of_speech_sensitivity   = _end_sens,
                )
            )
            _vad_applied = True
            await log_ws("ok", "VAD",
                         "Tier-2B AutomaticActivityDetection (no silence_ms): end=LOW")
        except (AttributeError, TypeError, Exception):
            pass

    # Tier 3 — AutomaticActivityDetection minimal (disabled=False saja, no sensitivity enums)
    if not _vad_applied:
        try:
            live_cfg_kwargs["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                )
            )
            _vad_applied = True
            await log_ws("ok", "VAD", "Tier-3 minimal AutomaticActivityDetection")
        except (AttributeError, TypeError, Exception):
            pass

    if not _vad_applied:
        await log_ws("warn", "VAD",
                     "VAD config not supported — Gemini default active. "
                     "Upgrade google-genai >= 1.7. "
                     f"Target: silence={_silence_ms}ms end=LOW start=LOW")

    try:
        live_cfg_kwargs["input_audio_transcription"]  = types.AudioTranscriptionConfig()
        live_cfg_kwargs["output_audio_transcription"] = types.AudioTranscriptionConfig()
        await log_ws("ok", "SERVER", "Transcription ON")
    except AttributeError:
        await log_ws("warn", "SERVER", "Transcription unavailable in this SDK version")

    live_cfg = types.LiveConnectConfig(**live_cfg_kwargs)

    try:
        async with client.aio.live.connect(
            model=settings.LIVE_MODEL, config=live_cfg
        ) as session:
            await log_ws("ok", "GEMINI", "Session opened")
            await send_json({"type": "session_ready"})

            current_page = "home"
            _processed_frag_keys: set[frozenset] = set()
            _session_turn_count: int = 0

            # v21: Generation counter — abort stale tasks after Flash
            _gen: list[int] = [0]
            # Early Flash result cache
            _early_cache: list = [None]
            # LOG-2-FIX: flag untuk mencegah context_refresh konflik dengan
            # placeholder turn yang sedang terbuka (in-flight RAG request).
            # True = hold gate sudah dikirim, False = sudah selesai/tidak ada.
            _rag_in_flight: list[bool] = [False]
            # UI PANEL FIX: track whether the repository panel is currently visible
            # in the browser. When True, open_repository searches ALWAYS refresh
            # the panel even without an explicit "open repository" voice command.
            # Updated whenever an fe_payload action opens or closes the panel.
            _panel_open: list[bool] = [False]

            # ── Browser → Gemini ───────────────────────────────────────
            async def recv_browser():
                nonlocal current_page
                try:
                    while True:
                        raw = await websocket.receive_text()
                        msg = json.loads(raw)

                        if msg["type"] == "realtimeInput":
                            pcm = base64.b64decode(msg["audioData"])
                            await session.send(
                                input=types.LiveClientRealtimeInput(
                                    media_chunks=[
                                        types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                                    ]
                                )
                            )

                        elif msg["type"] == "page_update":
                            current_page = msg.get("page", "home")
                            log.debug("Page updated: %s", current_page)

                except WebSocketDisconnect:
                    await log_ws("warn", "WS", "Browser disconnected")
                except Exception as e:
                    await log_ws("error", "B→G", str(e))

            # ── Gemini → Browser ───────────────────────────────────────
            async def recv_gemini():
                nonlocal current_page, _session_turn_count
                turn          = 0
                chunks        = 0
                user_frags:   list[str] = []
                asst_frags:   list[str] = []

                _early_task:        asyncio.Task | None = None
                _final_task:        asyncio.Task | None = None
                _interrupted_task:  asyncio.Task | None = None
                _early_flash_fired: bool = False
                _last_cancel_idx:   int  = 0
                _early_fired_at_n:  int  = 0
                _early_refire_min:  int  = 0
                # FIX double-nav: track which intents already fired from recv_gemini
                # so _process_turn PATH 2 can skip them. Reset each turn alongside user_frags.
                _nav_fired_this_turn: set[str] = set()

                def _set_refire_min(n: int) -> None:
                    nonlocal _early_refire_min, _early_task
                    _early_refire_min = n
                    _early_task = None

                while True:
                    try:
                        async for resp in session.receive():
                            sc = getattr(resp, "server_content", None)

                            if sc:
                                # ── User ASR fragment ──────────────────
                                # BUG-FIX #1: getattr returns None (not "") when
                                # attribute exists but is set to None (common during
                                # silence/aborted chunks). Use `or ""` to guard.
                                it = getattr(sc, "input_transcription", None)
                                if it:
                                    txt = (getattr(it, "text", None) or "").strip()
                                    if txt:
                                        user_frags.append(txt)
                                        await send_json({"type": "transcript_user", "text": txt})

                                        # Cancel check
                                        for frag in user_frags[_last_cancel_idx:]:
                                            fl = frag.lower().strip().rstrip(".,!?")
                                            if fl in _CANCEL_SET or any(
                                                cw in fl for cw in _CANCEL_SET
                                            ):
                                                for task in (_early_task, _interrupted_task, _final_task):
                                                    if task and not task.done():
                                                        task.cancel()
                                                _early_task        = None
                                                _interrupted_task  = None
                                                _final_task        = None
                                                _early_flash_fired = False
                                                _last_cancel_idx   = 0
                                                await send_json({"type": "flush_audio"})
                                                await log_ws("warn", "CANCEL", f"Cancel: '{frag}'")
                                                user_frags = []
                                                _nav_fired_this_turn = set()
                                                # LOG-1-FIX: JANGAN kirim session.send() saat cancel.
                                                # Gemini Live menangani interrupted turn secara native.
                                                # Mengirim turn_complete=True di sini justru memicu
                                                # Gemini menjawab dengan konteks lama = double output.
                                                break
                                        else:
                                            _last_cancel_idx = len(user_frags)

                                        # ── Layer-1 instant nav fast-path ─────────
                                        if user_frags and not _early_flash_fired:
                                            joined    = " ".join(user_frags)
                                            norm_join = _asr_normalize(joined)
                                            fast_res  = fast_classify(
                                                norm_join, current_page,
                                                memory.last_intent, memory.last_topic,
                                            )
                                            if (
                                                fast_res is not None
                                                and not fast_res.get("_is_hint", False)
                                                and fast_res["intent"] in _NAV_FLASH_SKIP
                                            ):
                                                _early_flash_fired = True
                                                fk = frozenset(user_frags)
                                                _processed_frag_keys.add(fk)
                                                _nav_fired_this_turn.add(fast_res["intent"])
                                                asyncio.create_task(
                                                    _process_nav_fast(
                                                        fast_res, current_page,
                                                        send_json, log_ws
                                                    )
                                                )
                                                await log_ws("ok", "FAST",
                                                             f"Nav fast-path: {fast_res['intent']}")
                                                continue

                                        # ── Early Flash classification ─────────────
                                        n      = len(user_frags)
                                        joined = " ".join(user_frags)

                                        # Progressive re-fire support
                                        if (
                                            _early_flash_fired
                                            and _early_refire_min > 0
                                            and n >= _early_refire_min
                                            and not _early_task
                                        ):
                                            _early_flash_fired = False

                                        # v21-A: threshold diturunkan 3→2, 8→5
                                        if (
                                            n >= settings.EARLY_CLASSIFICATION_MIN_FRAGS
                                            and len(joined) >= settings.EARLY_CLASSIFICATION_MIN_CHARS
                                            and not _early_flash_fired
                                            and not _early_task
                                        ):
                                            fk = frozenset(user_frags)
                                            if fk not in _processed_frag_keys:
                                                _early_flash_fired = True
                                                _early_fired_at_n  = n
                                                _early_refire_min  = 0
                                                _gen[0] += 1
                                                _early_cache[0] = None
                                                _early_task = asyncio.create_task(
                                                    _process_turn(
                                                        list(user_frags), current_page,
                                                        session, websocket, send_json, log_ws,
                                                        label="early",
                                                        processed_keys=_processed_frag_keys,
                                                        gen_snap=_gen[0],
                                                        gen_ref=_gen,
                                                        early_cache=_early_cache,
                                                        refire_callback=lambda: _set_refire_min(n + 4),
                                                        nav_fired=_nav_fired_this_turn,
                                                        rag_in_flight=_rag_in_flight,
                                                        panel_open=_panel_open,
                                                    )
                                                )

                                # ── Assistant ASR fragment ─────────────
                                # BUG-FIX #1 (part 2): same None guard
                                ot = getattr(sc, "output_transcription", None)
                                if ot:
                                    txt = (getattr(ot, "text", None) or "").strip()
                                    if txt:
                                        asst_frags.append(txt)
                                        await send_json({"type": "transcript_assistant",
                                                         "text": txt})

                                # ── Interrupted ───────────────────────
                                if getattr(sc, "interrupted", False):
                                    for task in (_early_task, _interrupted_task, _final_task):
                                        if task and not task.done():
                                            task.cancel()
                                    _early_task        = None
                                    _interrupted_task  = None
                                    _final_task        = None
                                    _early_flash_fired = False
                                    _last_cancel_idx   = 0
                                    # LOG-5-FIX: reset STM intent jika interrupted sebelum
                                    # _process_turn chitchat branch sempat memanggil reset_stm().
                                    # Ini mencegah last_intent stagnan (e.g. 'fetch_news' bertahan
                                    # terlalu lama dan memicu PATH 1C false positive).
                                    if memory.last_intent in ("chitchat", ""):
                                        memory.reset_stm()
                                    await send_json({"type": "flush_audio"})
                                    await send_json({"type": "interrupted"})
                                    await log_ws("warn", "SESSION", "Interrupted by user")
                                    chunks     = 0
                                    asst_frags = []

                                    if user_frags:
                                        frags_snap = list(user_frags)
                                        page_snap  = current_page
                                        user_frags = []
                                        _gen[0] += 1
                                        _early_cache[0] = None
                                        _interrupted_task = asyncio.create_task(
                                            _process_turn(
                                                frags_snap, page_snap,
                                                session, websocket, send_json, log_ws,
                                                label="interrupted",
                                                processed_keys=_processed_frag_keys,
                                                gen_snap=_gen[0],
                                                gen_ref=_gen,
                                                early_cache=None,
                                                nav_fired=_nav_fired_this_turn,
                                                rag_in_flight=_rag_in_flight,
                                                panel_open=_panel_open,
                                            )
                                        )
                                    continue

                                # ── Turn complete ──────────────────────
                                if getattr(sc, "turn_complete", False):
                                    turn += 1
                                    _session_turn_count += 1
                                    full_user = " ".join(user_frags).strip()
                                    full_asst = " ".join(asst_frags).strip()

                                    if full_user: memory.add_turn("user",      full_user)
                                    if full_asst: memory.add_turn("assistant", full_asst)

                                    frags_snap = list(user_frags)
                                    page_snap  = current_page
                                    completed  = chunks
                                    n_frags    = len(user_frags)
                                    user_frags = []
                                    asst_frags = []
                                    chunks     = 0
                                    _last_cancel_idx     = 0
                                    _nav_fired_this_turn = set()  # FIX double-nav

                                    _processed_frag_keys.clear()

                                    await send_memory_stats()
                                    await send_json({
                                        "type":   "turn_complete",
                                        "turn":   turn,
                                        "chunks": completed,
                                        "frags":  n_frags,
                                    })

                                    # Cancel outstanding tasks — final re-classifies with full frags
                                    for task in (_early_task, _interrupted_task, _final_task):
                                        if task and not task.done():
                                            task.cancel()
                                    _early_task        = None
                                    _interrupted_task  = None
                                    _early_flash_fired = False

                                    # v29 Opsi 2: Hold gate DIHAPUS sepenuhnya.
                                    # Alasan: hold gate tidak pernah berhasil mencegah
                                    # Gemini generate karena tiba SETELAH Gemini sudah
                                    # committed (~50ms setelah turn_complete server event).
                                    # Kita TERIMA bahwa Gemini akan bersuara singkat dari
                                    # stale context. Context injection yang benar dikirim
                                    # setelah Flash+ES selesai sebagai turn baru eksplisit.
                                    _holdgate_sent_early = False  # always False now

                                    if _session_turn_count % 3 == 1:
                                        await send_memory_stats()

                                    # NEW-1-FIX: context_refresh DIHAPUS.
                                    # Baik turn_complete=True (double output) maupun
                                    # turn_complete=False (Gemini silent selamanya) bermasalah.
                                    # System prompt + per-query RAG injection sudah cukup.
                                    # _session_turn_count tetap dihitung untuk statistik.

                                    _gen[0] += 1
                                    _final_task = asyncio.create_task(
                                        _process_turn(
                                            frags_snap, page_snap,
                                            session, websocket, send_json, log_ws,
                                            label="final",
                                            processed_keys=_processed_frag_keys,
                                            gen_snap=_gen[0],
                                            gen_ref=_gen,
                                            early_cache=_early_cache,
                                            nav_fired=_nav_fired_this_turn,
                                            rag_in_flight=_rag_in_flight,
                                            holdgate_sent=_holdgate_sent_early,
                                            panel_open=_panel_open,
                                        )
                                    )
                                    break

                            # Audio chunk
                            if resp.data:
                                chunks += 1
                                b64 = base64.b64encode(resp.data).decode()
                                await send_json({"type": "audioStream", "data": b64})

                            if resp.text:
                                await send_json({"type": "textStream", "data": resp.text})
                                asst_frags.append(resp.text)

                    except WebSocketDisconnect:
                        await log_ws("warn", "G→B", "Browser disconnected")
                        return
                    except Exception as e:
                        err_str = str(e)
                        # v22.3: Bedakan error fatal vs sementara.
                        # Kode 1011 (Internal Error), 1008 (Policy Violation),
                        # 1003 (Unsupported Data), 1001 (Going Away) = fatal,
                        # dikirim oleh Gemini server saat koneksi tidak bisa dilanjutkan.
                        _FATAL_WS_SIGNALS = (
                            "1011", "1008", "1003", "1001",
                            "internal error", "going away",
                            "invaliddata", "connection closed",
                        )
                        is_fatal = any(
                            sig in err_str.lower()
                            for sig in _FATAL_WS_SIGNALS
                        )
                        if is_fatal:
                            await log_ws("error", "G→B",
                                         f"Gemini session fatal ({err_str[:60]}). "
                                         f"Reconnect needed.")
                            await send_json({
                                "type":    "gemini_error",
                                "code":    "session_fatal",
                                "message": "Session terminated. Please reload the page to continue.",
                            })
                            return  # break recv_gemini loop — jangan retry
                        # Error sementara (network blip, timeout ringan) → sleep & retry
                        await log_ws("error", "G→B", str(e))
                        await asyncio.sleep(0.1)

            await asyncio.gather(recv_browser(), recv_gemini())

    except WebSocketDisconnect:
        log.warning("Browser disconnected before session opened")
    except Exception as e:
        err_str = str(e)
        log.exception("Session fatal error")
        # Kirim pesan yang tepat ke browser berdasarkan jenis error
        if any(x in err_str.upper() for x in
               ("RESOURCE_EXHAUSTED", "429", "QUOTA", "RATE_LIMIT")):
            await send_json({"type": "quota_error",
                             "message": "Gemini API quota exceeded. Please wait a few minutes and reload."})
        elif any(x in err_str for x in ("1011", "1008", "1003", "internal error")):
            await send_json({"type": "gemini_error",
                             "code": "session_fatal",
                             "message": "Gemini session terminated (server error). Please reload."})
        else:
            await send_json({"type": "log", "level": "error", "tag": "SESSION",
                             "data": f"Fatal: {err_str}"})

    # Post-session: consolidate memory
    log.info("Session ended — starting memory consolidation")
    _start_consolidation(memory)


# ══════════════════════════════════════════════════════════════════════════════
#  Context refresh helper
# ══════════════════════════════════════════════════════════════════════════════
async def _inject_context_refresh(
    session,
    send_json,
    log_ws,
    turn_count: int,
    rag_in_flight: list | None = None,
) -> None:
    # LOG-2-FIX: Double-check in-flight at execution time
    if rag_in_flight is not None and rag_in_flight[0]:
        await log_ws("debug", "CTX",
                     f"Context refresh skipped turn {turn_count} — RAG in-flight")
        return
    try:
        recent = memory.recent_turns_for_refresh(n=6)
        # LOG-2-FIX: turn_complete=False — refresh dikirim sebagai background context,
        # BUKAN sebagai user turn yang menuntut Gemini merespons.
        # Sebelumnya (turn_complete=True) menyebabkan Gemini menjawab refresh sebagai
        # turn mandiri → kemudian menjawab RAG context juga = DOUBLE OUTPUT.
        # Dengan turn_complete=False, refresh terakumulasi sebagai konteks dan Gemini
        # hanya merespons ketika user turn berikutnya tiba secara natural.
        refresh_msg = (
            f"[SISTEM: CONTEXT REFRESH — Turn {turn_count}. "
            f"Ini pengingat internal — JANGAN direspons, DIAM saja.]\n"
            f"Rules: answer BRIEFLY 1-2 sentences, no bullets.\n"
            f"ANTI-HALLUCINATION: Answer ONLY from database context.\n"
            f"Recent conversation:\n{recent}"
        )
        await session.send(
            input=types.LiveClientContent(
                turns=[types.Content(
                    role="user",
                    parts=[types.Part(text=refresh_msg)]
                )],
                turn_complete=False,
            )
        )
        await log_ws("ok", "CTX",
                     f"Context refresh injected at turn {turn_count} ({len(refresh_msg)} chars)")
    except Exception as e:
        await log_ws("warn", "CTX", f"Context refresh failed (non-critical): {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  PATH 2: Nav-only fast handler
# ══════════════════════════════════════════════════════════════════════════════
async def _process_nav_fast(
    fast_result: dict,
    page:        str,
    send_json,
    log_ws,
) -> None:
    intent = fast_result["intent"]

    if intent == "back_to_repository":
        await send_json({"type": "tool_call", "action": "back_to_repository"})
        await log_ws("nav", "TOOL", "back_to_repository (no Flash)")
        memory.mark_nav_action()   # v22.5: preserve RAG context after nav
        return

    if intent == "close_panel":
        await send_json({"type": "tool_call", "action": "close_panel"})
        await log_ws("nav", "TOOL", "close_panel (no Flash)")
        memory.mark_nav_action()   # v22.5: preserve RAG context after nav
        return

    if intent == "highlight_keywords":
        kw = fast_result.get("keywords_id") or fast_result.get("keywords") or []
        await send_json({"type": "tool_call",
                         "action": "highlight_keywords", "keywords": kw})
        await log_ws("nav", "TOOL", f"highlight_keywords kw={kw} (no Flash)")
        return


# ══════════════════════════════════════════════════════════════════════════════
#  Per-turn processing: Six-path routing (v21)
# ══════════════════════════════════════════════════════════════════════════════
async def _process_turn(
    frags:            list[str],
    page:             str,
    session,
    websocket:        WebSocket,
    send_json,
    log_ws,
    label:            str        = "final",
    processed_keys:   set | None = None,
    classify_sem:     asyncio.Semaphore | None = None,  # kept for API compat
    gen_snap:         int        = 0,
    gen_ref:          list | None = None,
    early_cache:      list | None = None,
    refire_callback:  object     = None,
    nav_fired:        set | None = None,   # FIX double-nav: intents already fired
    rag_in_flight:    list | None = None,  # LOG-2-FIX: shared flag for context_refresh gate
    holdgate_sent:    bool        = False, # NEW-2-FIX: hold gate already sent in recv_gemini
    panel_open:       list | None = None,  # UI FIX: shared flag — True when repository panel is visible
) -> None:
    """
    Six-path turn processor v21.0:

    PATH 1  — Chitchat    : Layer-0 skip → 0ms → Gemini answers naturally
    PATH 1B — Anaphoric   : STM pronoun ref → Flash + STM → voice answer
    PATH 1C — STM follow-up: Short sentence after RAG → Flash (Rule 0A)
    PATH 2  — Nav only    : Layer-1 fast → instant UI action
    PATH 3  — fetch_news  : Flash → RSS → voice (panel only if explicit)
    PATH 4  — RAG (doc)   : Flash ‖ ES parallel → inject context + panel if UI intent
    """
    if not frags:
        return

    joined = " ".join(frags).strip()

    # ── Stale-task abort helper ──────────────────────────────────────────
    def _stale() -> bool:
        return gen_ref is not None and gen_ref[0] != gen_snap

    # ── Deduplication ────────────────────────────────────────────────────
    frag_key = frozenset(frags)
    if processed_keys is not None:
        if frag_key in processed_keys:
            await log_ws("debug", f"TURN[{label}]", "Duplicate frag set — skipped")
            return
        processed_keys.add(frag_key)

    await log_ws("flash", f"TURN[{label}]", f"'{joined[:80]}' …")

    normalized = _asr_normalize(joined)

    # Language detection — used later to append correct language instruction to context messages.
    # Prevents Gemini from mirroring the Indonesian context injection and replying in ID
    # when the user spoke EN. Simple ASCII-ratio heuristic: no external dependency needed.
    _user_lang: str = _detect_lang(joined)

    # ── PATH 1 + 1B + 1C: Layer-0 fast-path ─────────────────────────────
    if settings.FAST_CHITCHAT_SKIP_FLASH:
        if not needs_flash(joined) and not needs_flash(normalized):
            # PATH 1B: Anaphoric reference → Flash untuk STM resolution
            if has_anaphoric(joined) or has_anaphoric(normalized):
                await log_ws("info", f"TURN[{label}]",
                             "PATH 1B: anaphoric detected → Flash for STM resolution")
            # PATH 1C: STM follow-up → Flash untuk Rule 0A
            elif has_stm_followup(joined, memory.last_intent) or \
                 has_stm_followup(normalized, memory.last_intent):
                await log_ws("info", f"TURN[{label}]",
                             f"PATH 1C: STM follow-up after '{memory.last_intent}' → Flash")
            else:
                # PATH 1: Pure chitchat
                await log_ws("info", f"TURN[{label}]",
                             "PATH 1: chitchat (Layer-0 skip, no Flash)")
                await send_json({
                    "type": "flash_result",
                    "data": {
                        "intent": "chitchat", "reconstructed": normalized,
                        "search_query": "", "reasoning": "Layer-0 fast-path",
                        "keywords": [], "path": 1,
                    },
                })
                return

    # ── Combined Layer-1 hint (PATH 2 nav + ES prefetch) ─────────────────
    loop = asyncio.get_running_loop()
    hint = fast_classify(normalized, page, memory.last_intent, memory.last_topic)
    prefetch_task: asyncio.Task | None = None

    # PATH 2: instant nav fast-path (no Flash needed)
    if (
        settings.LAYER1_SKIP_FLASH_NAV
        and hint is not None
        and not hint.get("_is_hint", False)
        and hint["intent"] in _NAV_FLASH_SKIP
    ):
        # FIX double-nav: if recv_gemini already fired this intent for this turn,
        # skip silently. The UI action was already sent; running it again wastes
        # tokens and causes duplicate highlights/closes.
        if nav_fired is not None and hint["intent"] in nav_fired:
            await log_ws("debug", f"TURN[{label}]",
                         f"PATH 2 skipped — nav '{hint['intent']}' already fired this turn")
            return
        await log_ws("info", f"TURN[{label}]",
                     f"PATH 2: nav fast-path {hint['intent']} (no Flash)")
        await send_json({"type": "flash_result", "data": {**hint, "path": 2}})
        await _process_nav_fast(hint, page, send_json, log_ws)
        return

    # FIX-1 (Triage Paralysis) & FIX-7 (Race Condition):
    # _HOLD_GATE keeps Gemini turn open (turn_complete=False) so Gemini waits
    # for real context before speaking. A single space " " satisfies the non-empty
    # API requirement while producing zero TTS audio.
    #
    # FIX-1: Every exit path MUST close the placeholder turn via _close_placeholder()
    # so Gemini is never left permanently stuck in waiting state. Previously several
    # exception / stale-abort paths returned without closing the open turn.
    #
    # FIX-7 (Race Condition): _placeholder_gen tracks the generation ID when the
    # placeholder was opened. If gen changes before we close (new user turn arrived,
    # old task was cancelled), we skip the close — the new task owns the session.
    _HOLD_GATE = " "
    _rag_intents_that_need_search = frozenset({
        "open_repository", "regulation_query", "preview_document", "fetch_news"
    })
    _placeholder_sent = False
    _placeholder_gen  = gen_snap  # FIX-7: snapshot gen at placeholder open time

    async def _close_placeholder(reason: str = "", send_session: bool = False) -> None:
        """
        LOG-1-FIX: Placeholder close TIDAK lagi mengirim session.send() secara default.

        Masalah sebelumnya: setiap _close_placeholder(' ', turn_complete=True)
        membuka turn baru di Gemini Live → Gemini menjawab dengan konteks lama
        (stale) sebagai output pertama, lalu menjawab RAG context yang benar
        sebagai output kedua = DOUBLE OUTPUT.

        Solusi: Gemini Live menangani interrupted/aborted turn secara native.
        Kita TIDAK perlu mengirim turn_complete=True untuk "menutup" turn yang
        dibatalkan. Hanya satu jalur yang boleh mengirim ke Gemini:
        context injection (send_session=True), karena di situ Gemini MEMANG
        harus merespons dengan konten yang benar.

        Semua jalur lain (stale, cancel, nav, error) → send_session=False (default).

        FIX-7: Skip jika gen sudah berubah (stale task).
        """
        nonlocal _placeholder_sent
        if not _placeholder_sent:
            return
        # FIX-7: if gen moved on, do NOT act — another task owns the session
        if gen_ref is not None and gen_ref[0] != _placeholder_gen:
            _placeholder_sent = False
            return
        _placeholder_sent = False
        # LOG-2-FIX: clear rag_in_flight flag saat placeholder ditutup
        if rag_in_flight is not None:
            rag_in_flight[0] = False
        if reason:
            await log_ws("debug", f"TURN[{label}]", f"Placeholder close: {reason}")
        if not send_session:
            # LOG-1-FIX: Jangan kirim apapun ke Gemini.
            # Turn yang sudah di-hold gate (turn_complete=False) akan
            # dibatalkan secara native oleh Gemini Live ketika user berbicara lagi.
            return
        # Hanya jalur context injection yang sampai di sini (send_session=True).
        # turn_complete=True menutup turn dan Gemini merespons dengan context nyata.
        try:
            await session.send(
                input=types.LiveClientContent(
                    turns=[types.Content(
                        role="user",
                        parts=[types.Part(text=" ")]
                    )],
                    turn_complete=True,
                )
            )
        except Exception as e:
            await log_ws("warn", f"TURN[{label}]",
                         f"Placeholder close (session send) failed: {e}")

    # v29 Opsi 2: Hold gate tidak pernah dikirim.
    # _placeholder_sent = False selalu → context injection tidak perlu "tutup" apapun.
    # Context dikirim langsung sebagai turn baru setelah data siap.
    if hint is not None and hint.get("intent") in _rag_intents_that_need_search:
        await log_ws("debug", f"TURN[{label}]",
                     f"RAG intent detected (hint={hint.get('intent')}), no hold gate (v29)")

    # ES pre-fetch jika hint mengarah ke RAG intent
    if (
        settings.PREFETCH_ES
        and hint is not None
        and hint.get("_is_hint", False)
        and hint["intent"] in ("open_repository", "regulation_query")
    ):
        # LOG-4-FIX: untuk preview_document, gunakan document_name dari hint
        # jika tersedia — jauh lebih akurat dari search_query yang bisa mengandung noise.
        _raw_pq = hint.get("document_name", "").strip() or hint.get("search_query", "")

        # v22.1-D / v22.3: Normalize + bersihkan prefetch query dari artefak ASR.
        _raw_pq = _asr_normalize(_raw_pq)

        _PREFETCH_NOISE: frozenset = frozenset({
            # Artefak navigasi
            "related", "like", "similar", "such", "about", "regarding",
            "concerning", "terkait", "seperti", "mirip", "tentang", "mengenai",
            "rep", "re", "repositor", "panel", "list", "daftar", "show",
            "find", "open", "buka", "tampilkan", "cari", "carikan",
            "document", "dokumen", "regulation", "peraturan", "regulasi",
            # Stopwords dasar yang lolos filter len>=3
            "the", "and", "for", "are", "but", "not", "was", "has", "had",
            "dan", "untuk", "yang", "dari", "ada", "dengan", "atau", "oleh",
            # v22.3: noise navigasi tambahan dari query voice
            "now", "okay", "please", "how", "let", "get",
            # LOG-4-FIX: kata kerja dan kata standalone yang tidak berguna sbg query ES
            "give", "help", "tell", "want", "need", "just", "query",
            "nomor",  # "nomor" tanpa angka di belakangnya = tidak berguna
            "tahun",  # "tahun" tanpa angka = tidak berguna
            "information", "info", "data", "content", "isi",
        })
        _clean_tokens = [
            t for t in _raw_pq.split()
            if len(t) >= 3 and t.lower() not in _PREFETCH_NOISE
        ]
        _raw_pq = " ".join(_clean_tokens)

        if not _raw_pq or (len(_clean_tokens) == 1 and len(_raw_pq) < 4):
            _raw_pq = memory.last_topic or ""

        prefetch_query = _raw_pq.strip()
        if prefetch_query:
            # BUG-FIX #8: Derive query_en from the original joined text so the
            # prefetch is bilingual — matching what execute_tool will call later.
            # If the prefetch query is Indonesian, query_en stays None (ES handles it).
            # If the query looks like English (ASCII-dominant), pass it as query_en
            # and let _expand_for_content_search add the Indonesian expansion.
            _is_latin = sum(1 for c in prefetch_query if c.isascii() and c.isalpha()) \
                        > len(prefetch_query) * 0.7
            _prefetch_query_en: str | None = prefetch_query if _is_latin else None
            await log_ws("info", f"TURN[{label}]",
                         f"ES prefetch started: q={prefetch_query!r} "
                         f"en={_prefetch_query_en!r} (parallel with Flash)")
            prefetch_task = asyncio.ensure_future(
                loop.run_in_executor(
                    None, _prefetch_get_data, prefetch_query, _prefetch_query_en
                )
            )
        else:
            await log_ws("debug", f"TURN[{label}]", "ES prefetch skipped — no valid query")

    # ── Flash classification ──────────────────────────────────────────────
    if _stale():
        if prefetch_task: prefetch_task.cancel()
        # FIX-1: close placeholder so Gemini is never left in waiting state
        await _close_placeholder("Dibatalkan — turn baru masuk.")
        await log_ws("debug", f"TURN[{label}]", "Stale before Flash — aborted")
        return

    # BUG-FIX #9: Filter non-domain noise fragments (e.g. Javanese bystander
    # speech like "duh tangane padha mbah ni") before passing to Flash.
    # Strategy: if a fragment has ZERO overlap with any domain keyword AND is
    # shorter than 6 tokens AND is not the only fragment, drop it.
    # This is conservative — we only drop fragments that are clearly off-domain
    # to avoid accidentally removing valid short queries.
    if len(frags) > 1:
        _DOMAIN_HINT_RE = re.compile(
            r"\b(ojk|bi\b|lps|bank|peraturan|regulation|dokumen|document|"
            r"news|berita|keuangan|finance|hukum|legal|pojk|pbi|padg|uu\b|pp\b|"
            r"indonesia|gubernur|kebijakan|policy|interest|rate|suku|bunga|"
            r"open|buka|find|cari|preview|search|show|tampilkan|"
            r"what|how|apa|jelaskan|explain|berapa)\b", re.I
        )
        filtered_frags = [
            f for f in frags
            if len(f.split()) >= 6                       # long frags always kept
            or _DOMAIN_HINT_RE.search(f)                 # has domain keyword
            or not re.search(r"[a-zA-Z]", f)            # pure numbers/symbols
        ]
        if filtered_frags and len(filtered_frags) < len(frags):
            dropped = [f for f in frags if f not in filtered_frags]
            await log_ws("debug", f"TURN[{label}]",
                         f"Noise frags dropped: {dropped}")
            frags = filtered_frags
            joined = " ".join(frags).strip()

    # Try to reuse early Flash result for final
    result: dict | None = None
    if label == "final" and early_cache is not None and early_cache[0] is not None:
        # FIX ROOT CAUSE B: early task may have set early_cache[0] = "__executed__"
        # (a string). The tuple unpack below would crash on a string.
        # Guard here BEFORE the unpack so the check at line ~1305 is also reachable.
        if early_cache[0] == "__executed__":
            early_cache[0] = None
            if prefetch_task:
                prefetch_task.cancel()
            await log_ws("info", f"TURN[{label}]",
                         "Skipped — early already executed (guard before unpack)")
            return
        cached_result, cached_fk = early_cache[0]
        early_cache[0] = None  # consume
        fk_now = frozenset(frags)
        if cached_fk == fk_now or (
            cached_fk.issubset(fk_now) and len(frags) - len(cached_fk) <= 3
        ):
            result = cached_result
            await log_ws("ok", f"TURN[{label}]",
                         f"Early cache hit → skipped Flash (intent={result.get('intent')})")

    if result is None:
        conv_ctx = memory.recent_turns_for_classifier(n=4)

        # v22.4: Strip <noise> tags from frags before Flash classification.
        _clean_frags = [
            f for f in frags
            if not re.match(r'^<[^>]+>$', f.strip())
            and f.strip() not in ('<noise>', '')
        ]
        _flash_frags = _clean_frags if _clean_frags else frags

        async def _run_flash():
            return await loop.run_in_executor(
                None,
                lambda: classifier.classify(
                    _flash_frags,
                    page,
                    conv_ctx,
                    memory.last_intent,
                    memory.last_topic,
                )
            )

        try:
            result = await _run_flash()
        except asyncio.CancelledError:
            if prefetch_task: prefetch_task.cancel()
            # BUG-A-FIX (Flash CancelledError): placeholder juga harus ditutup
            # jika Flash dibatalkan saat turn masih terbuka
            await _close_placeholder()
            return
        except Exception as e:
            await log_ws("error", f"TURN[{label}]", f"Classifier error: {e}")
            if prefetch_task: prefetch_task.cancel()
            await _close_placeholder()
            return

    if _stale():
        if prefetch_task: prefetch_task.cancel()
        # FIX-1: close placeholder
        await _close_placeholder("Dibatalkan — turn baru masuk.")
        await log_ws("debug", f"TURN[{label}]", "Stale after Flash — aborted")
        return

    if not isinstance(result, dict):
        await log_ws("error", f"TURN[{label}]", f"Classifier non-dict: {type(result)}")
        if prefetch_task: prefetch_task.cancel()
        # FIX-1: close placeholder
        await _close_placeholder("Maaf, terjadi kesalahan. Silakan coba lagi.")
        return

    intent = result.get("intent", "chitchat")
    sq     = result.get("search_query", "")
    kw_id  = result.get("keywords_id") or []

    # NEW-4-FIX: sq fallback ke last_topic jika Flash mengembalikan query sangat
    # pendek/tidak bermakna DAN intent adalah RAG intent DAN last_topic ada.
    # Contoh: "what is the answer?" → sq='answer' → bukan query yang berguna.
    # Dengan last_topic='finance', fallback ke 'finance' → ES hit lebih relevan.
    _SQ_NOISE_SINGLE = frozenset({
        "answer", "jawaban", "it", "that", "this", "them", "those",
        "itu", "ini", "mereka", "tersebut", "result", "hasil",
    })
    _RAG_INTENTS_SQ = frozenset({"regulation_query", "open_repository", "fetch_news"})
    if (
        intent in _RAG_INTENTS_SQ
        and memory.last_topic
        and sq
        and len(sq.split()) <= 1
        and sq.lower() in _SQ_NOISE_SINGLE
    ):
        await log_ws("info", f"TURN[{label}]",
                     f"sq={sq!r} too short/noise → fallback to last_topic={memory.last_topic!r}")
        sq = memory.last_topic
        result["search_query"] = sq
        if not kw_id:
            kw_id = sq.split()
            result["keywords_id"] = kw_id

    await log_ws(
        "flash", f"TURN[{label}]",
        f"→ intent:{intent} | sq:{sq!r} | kw_id:{kw_id} | "
        f"recon:'{result.get('reconstructed', '')[:60]}'",
    )
    await send_json({"type": "flash_result",
                     "data": {**result, "path": "deferred" if label == "early" else 4}})

    # ── Early guard: heavy intents wait for full sentence ─────────────────
    if label == "early" and intent in (
        "open_repository", "regulation_query", "preview_document",
        "detail_document", "fetch_news",
    ):
        can_execute_early = False

        if intent in ("preview_document", "detail_document"):
            doc_name = result.get("document_name") or ""
            if doc_name and _COMPLETE_DOC_RE.search(doc_name):
                can_execute_early = True
                await log_ws("ok", f"TURN[{label}]",
                             f"Complete doc name → execute early: {doc_name!r}")

        elif intent == "fetch_news":
            # fetch_news selalu bisa execute early
            can_execute_early = True
            await log_ws("ok", f"TURN[{label}]", "fetch_news executing early")

        elif intent == "open_repository":
            recon_early = result.get("reconstructed", "")
            # v21-A: execute early jika explicit open ATAU query sudah substansial
            meaningful_count = _count_meaningful_tokens(sq)
            if is_explicit_open(joined) or is_explicit_open(recon_early):
                can_execute_early = True
                await log_ws("ok", f"TURN[{label}]",
                             "open_repository explicit trigger → executing early")
            elif meaningful_count >= 2:
                can_execute_early = True
                await log_ws("ok", f"TURN[{label}]",
                             f"open_repository substansial query ({meaningful_count} tokens) → executing early")

        elif intent == "regulation_query":
            # voice-only, tidak butuh frag lengkap
            can_execute_early = True
            await log_ws("ok", f"TURN[{label}]",
                         "regulation_query executing early (voice-only)")

        if not can_execute_early:
            if early_cache is not None:
                early_cache[0] = (result, frozenset(frags))
            if refire_callback is not None:
                refire_callback()
            await log_ws("info", f"TURN[{label}]",
                         f"Intent {intent!r} cached → deferred, re-fire armed")
            if prefetch_task: prefetch_task.cancel()
            # FIX-1: close placeholder so Gemini doesn't get stuck on deferred early turn
            await _close_placeholder("Menunggu kalimat lengkap.")
            return

    # ── PATH 1 (Flash confirmed chitchat) ─────────────────────────────────
    if intent == "chitchat":
        await log_ws("info", f"TURN[{label}]", "PATH 1: chitchat (Flash confirmed)")
        if prefetch_task:
            prefetch_task.cancel()

        # FIX-1: close placeholder via centralised helper
        # Uses chitchat-specific text so Gemini knows to answer naturally
        await _close_placeholder(
            "Pertanyaan ini adalah obrolan biasa — jawab secara natural tanpa merujuk database."
        )

        # FIX-3 (Context Blindness): STM soft-reset ONLY on final/interrupted turns
        # AND only when the user hasn't just navigated (close/back).
        # memory.reset_stm() is now a SOFT reset — it preserves last_topic if locked,
        # preventing the AI from forgetting the active regulation topic after a single
        # ambiguous chitchat-classified turn.
        _RAG_CONTEXT_INTENTS = frozenset({
            "fetch_news", "open_repository", "regulation_query", "preview_document"
        })
        if (
            label != "early"
            and not memory.nav_just_happened
            and settings.STM_CHITCHAT_CLEAR
            and memory.last_intent in _RAG_CONTEXT_INTENTS
        ):
            # FIX-3: use soft reset so topic is preserved if locked
            memory.reset_stm()
            await log_ws("info", f"TURN[{label}]",
                         f"STM soft-reset after chitchat (topic_locked={memory.stm_topic_locked}, "
                         f"last_topic={memory.last_topic!r})")

            # v28: CHITCHAT_AFTER_RAG_CONTEXT_INJECT dinonaktifkan.
            # session.send() ke Gemini saat chitchat setelah RAG memicu double output
            # karena turn mungkin masih terbuka dari hold gate path lain.
            # Gemini sudah menerima context injection yang benar — tidak perlu reset manual.
            await log_ws("debug", f"TURN[{label}]",
                         "Chitchat after RAG — context reset skipped (v28 fix)")
        return

    # ── Navigation-only intents ────────────────────────────────────────────
    if intent == "close_panel":
        # LOG-6-FIX: cek nav_fired agar tidak double-fire jika fast-path sudah duluan
        if nav_fired is not None and "close_panel" in nav_fired:
            await log_ws("debug", f"TURN[{label}]", "close_panel skip — already fired this turn")
            if prefetch_task: prefetch_task.cancel()
            await _close_placeholder()
            return
        await send_json({"type": "tool_call", "action": "close_panel"})
        await log_ws("nav", "TOOL", "close_panel")
        memory.mark_nav_action()
        if nav_fired is not None: nav_fired.add("close_panel")
        if prefetch_task: prefetch_task.cancel()
        await _close_placeholder()
        return

    if intent == "back_to_repository":
        # LOG-6-FIX: cek nav_fired
        if nav_fired is not None and "back_to_repository" in nav_fired:
            await log_ws("debug", f"TURN[{label}]", "back_to_repository skip — already fired this turn")
            if prefetch_task: prefetch_task.cancel()
            await _close_placeholder()
            return
        await send_json({"type": "tool_call", "action": "back_to_repository"})
        await log_ws("nav", "TOOL", "back_to_repository")
        memory.mark_nav_action()
        if nav_fired is not None: nav_fired.add("back_to_repository")
        if prefetch_task: prefetch_task.cancel()
        await _close_placeholder()
        return

    if intent == "highlight_keywords":
        kw = result.get("keywords_id") or result.get("keywords_en") or []
        await send_json({"type": "tool_call",
                         "action": "highlight_keywords", "keywords": kw})
        await log_ws("nav", "HL", f"kw:{kw}")
        if prefetch_task: prefetch_task.cancel()
        await _close_placeholder("Keyword ditemukan.")
        return

    # ── Tool execution ────────────────────────────────────────────────────
    tool_info = intent_to_tool_args(intent, result, last_topic=memory.last_topic)
    if not tool_info:
        await log_ws("warn", f"TURN[{label}]", f"No tool mapped for intent={intent}")
        if prefetch_task: prefetch_task.cancel()
        # FIX-1: close placeholder
        await _close_placeholder("Tidak ada data ditemukan untuk permintaan ini.")
        return

    if not (isinstance(tool_info, tuple) and len(tool_info) == 2):
        await log_ws("error", f"TURN[{label}]",
                     f"intent_to_tool_args bad type: {type(tool_info)}")
        if prefetch_task: prefetch_task.cancel()
        # FIX-1: close placeholder
        await _close_placeholder("Error internal. Silakan coba lagi.")
        return

    tool_name, tool_args = tool_info

    # ── Panel opening policy (v21) ────────────────────────────────────────
    if intent in _ALWAYS_PANEL:
        open_panel = True
        await log_ws("nav", "PANEL", f"UI intent {intent!r} → panel ALWAYS opens")
    elif intent == "open_repository":
        recon_text = result.get("reconstructed", "")
        if settings.VOICE_ONLY_RAG:
            # UI PANEL FIX: If the repository panel is already visible (_panel_open[0]=True),
            # always refresh it with the new search results — no need for an explicit
            # "open repository" command. This prevents the panel from staying stale
            # (showing old documents) when the user asks for a different topic.
            _panel_already_open = panel_open is not None and panel_open[0]
            open_panel = _panel_already_open or is_explicit_open(joined) or is_explicit_open(recon_text)
            if _panel_already_open:
                await log_ws("nav", "PANEL", "open_repository auto-refresh (panel already visible)")
            elif open_panel:
                await log_ws("nav", "PANEL", "open_repository explicit → panel opens")
            else:
                await log_ws("nav", "PANEL",
                             "open_repository voice-only (say 'show repository' to open panel)")
        else:
            open_panel = True
    elif intent == "fetch_news":
        recon_text = result.get("reconstructed", "")
        if settings.FETCH_NEWS_VOICE_ONLY:
            open_panel = is_explicit_open(joined) or is_explicit_open(recon_text)
            if open_panel:
                await log_ws("nav", "PANEL", "fetch_news explicit panel open")
            else:
                await log_ws("nav", "PANEL", "fetch_news voice-only (no panel)")
        else:
            open_panel = True
    elif intent == "regulation_query":
        open_panel = False
        await log_ws("nav", "PANEL", "regulation_query voice-only ES (no panel)")
    elif settings.VOICE_ONLY_RAG:
        recon_text = result.get("reconstructed", "")
        open_panel = is_explicit_open(joined) or is_explicit_open(recon_text)
        if open_panel:
            await log_ws("nav", "PANEL", f"Explicit open panel: '{recon_text[:60] or joined[:60]}'")
    else:
        open_panel = True

    await log_ws("info", "TOOL",
                 f"Executing {tool_name!r} open_panel={open_panel} "
                 f"args={list(tool_args.keys())}")

    # ── Resolve ES prefetch ───────────────────────────────────────────────
    pre_fetched = None
    if prefetch_task is not None and intent in ("open_repository", "regulation_query"):
        if prefetch_task.done():
            try:
                pre_fetched = prefetch_task.result()
            except Exception:
                pre_fetched = None
        else:
            try:
                pre_fetched = await asyncio.wait_for(
                    asyncio.shield(prefetch_task), timeout=2.0
                )
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pre_fetched = None

        # BUG #8 FIX: The prefetch was started using the Layer-1 hint query, but Flash
        # may have returned a significantly different search_query. If the queries are
        # very different, the prefetch result is stale/irrelevant — discard it so
        # execute_tool does a fresh ES call with the correct Flash query.
        # Strategy: compare tool_args query (from Flash) with whatever was prefetched.
        # If they share at least one meaningful token → accept prefetch (same topic).
        # If zero token overlap → discard (different topic, prefetch would corrupt results).
        if pre_fetched is not None and tool_args:
            _flash_q   = (tool_args.get("query") or "").lower()
            _hint_q    = hint.get("search_query", "").lower() if hint else ""
            _flash_tok = set(t for t in _flash_q.split() if len(t) > 2)
            _hint_tok  = set(t for t in _hint_q.split()  if len(t) > 2)
            _tok_overlap = _flash_tok & _hint_tok
            if not _tok_overlap and _flash_q and _hint_q:
                await log_ws("debug", f"TURN[{label}]",
                             f"Prefetch discarded — Flash query {_flash_q!r} ≠ hint query {_hint_q!r} (0 token overlap)")
                pre_fetched = None
            else:
                await log_ws("debug", f"TURN[{label}]",
                             f"Prefetch accepted — overlap tokens: {_tok_overlap or '(queries same)'}")

    # ── Execute tool ──────────────────────────────────────────────────────
    try:
        llm_context, fe_payload = await loop.run_in_executor(
            None, execute_tool, tool_name, tool_args, open_panel, pre_fetched
        )
    except asyncio.CancelledError:
        # BUG-A-FIX: task dibatalkan (user bicara lagi saat ES running).
        # Placeholder turn masih terbuka → WAJIB ditutup agar Gemini tidak stuck.
        await _close_placeholder()
        return
    except Exception as e:
        await log_ws("error", "TOOL", f"execute_tool error: {e}")
        await _close_placeholder()
        return

    # BUG-FIX #5: Mark early_cache so final task knows early already executed.
    if label == "early" and early_cache is not None and settings.SKIP_FINAL_IF_EARLY_EXECUTED:
        early_cache[0] = "__executed__"

    # ── Update STM after successful tool execution ─────────────────────────
    _STM_NOISE = frozenset({
        "dokumen", "buka", "repositori", "panel", "daftar",
        "tampilkan", "document", "open", "repository", "show", "list", "page",
        "the", "a", "an", "dan", "untuk", "yang",
    })
    _raw_topic = (
        tool_args.get("query")
        or tool_args.get("kind2")
        or sq
        or ""
    )
    _filtered_tokens = [w for w in _raw_topic.split() if w.lower() not in _STM_NOISE]
    topic_for_stm = " ".join(_filtered_tokens) if _filtered_tokens else (memory.last_topic or _raw_topic)

    # v22-B / v22.5: Collect results for anaphoric reference resolution.
    _stm_results: list = []
    if fe_payload:
        _stm_results = (
            fe_payload.get("_stm_articles") or   # v22.5: voice-only fetch_news
            fe_payload.get("articles")      or
            fe_payload.get("documents")     or
            []
        )

    if intent not in ("chitchat", "close_panel", "back_to_repository"):
        # BUG-E-FIX: unlock_topic() jika topik baru berbeda signifikan dari last_topic.
        # Sebelumnya topic_lock tidak pernah di-unlock selama session (hanya di consolidate),
        # sehingga topik lama bisa "bocor" ke query berbeda terlalu lama.
        #
        # Aturan unlock:
        #   - Hanya jika intent adalah RAG intent (bukan nav/chitchat)
        #   - Hanya jika topic_for_stm non-empty DAN berbeda dari last_topic
        #   - "Berbeda" didefinisikan: tidak ada overlap token >= 1 antara dua topik
        #     (konservatif — hanya unlock jika betul-betul topik berbeda)
        _prev_topic = memory.last_topic
        if (
            memory.stm_topic_locked
            and topic_for_stm
            and _prev_topic
            and topic_for_stm.lower() != _prev_topic.lower()
        ):
            # BUG-X2-FIX: Token-overlap check diperluas dengan acronym awareness.
            # Masalah lama: "GWM" vs "giro wajib minimum" → tidak ada overlap token
            # padahal keduanya topik yang sama (akronim vs kepanjangan).
            # Solusi: sebelum membandingkan, expand acronym di kedua topik menggunakan
            # _ACRONYM_EXPAND_MAP. Juga normalkan EN/ID pairs yang umum.
            #
            # Aturan unlock hanya aktif jika:
            #   1. Tidak ada token overlap setelah expansion, DAN
            #   2. Panjang topik baru >= 2 token (single-token topic terlalu ambigu)
            from app.function_calling import _ALL_ACRONYM_MAP as _ACR_MAP
            def _expand_topic(t: str) -> str:
                result = t.lower()
                for k, v in _ACR_MAP.items():
                    result = result.replace(k.lower(), (k + " " + v).lower())
                return result

            _prev_expanded = _expand_topic(_prev_topic)
            _new_expanded  = _expand_topic(topic_for_stm)
            _prev_tokens   = set(_prev_expanded.split())
            _new_tokens    = set(_new_expanded.split())
            _overlap       = _prev_tokens & _new_tokens

            # Hanya unlock jika: tidak ada overlap DAN topik baru >= 5 karakter
            # BUG #7 FIX: Sebelumnya kondisinya >= 2 token, sehingga topik valid seperti
            # "makroprudensial" (17 karakter, 1 token) tidak pernah bisa unlock topik lama.
            # Fix: gunakan panjang karakter >= 5 sebagai proxy "topik cukup spesifik",
            # bukan jumlah token. Single-token topics seperti "OJK", "GWM", "PBI" tetap
            # cukup spesifik untuk di-unlock jika tidak ada overlap dengan topik lama.
            _topic_len = len(topic_for_stm.replace(" ", ""))
            if not _overlap and _topic_len >= 5:
                memory.unlock_topic()
                await log_ws("debug", "STM",
                             f"Topic unlocked: '{_prev_topic}' → '{topic_for_stm}' (no overlap after expansion)")
            elif not _overlap and _topic_len < 5:
                await log_ws("debug", "STM",
                             f"Topic unlock skipped: new topic '{topic_for_stm}' too short ({_topic_len} chars) — keeping lock")

        memory.update_stm(
            intent,
            topic_for_stm,
            results=_stm_results if _stm_results else None,
        )
        await log_ws("info", "STM",
                     f"Updated: intent={intent} topic={topic_for_stm!r} "
                     f"locked={memory.stm_topic_locked}")

    # ── Send UI payload to browser ────────────────────────────────────────
    # v22.5: Only send fe_payload if it has an "action" key.
    if fe_payload and fe_payload.get("action"):
        action = fe_payload.get("action")
        # UI PANEL FIX: keep panel_open flag in sync with what the browser actually shows.
        # open_repository → panel becomes visible.
        # open_deep_search / open_detail / close_panel → repository panel is no longer
        # the active view (replaced by doc preview, detail, or nothing).
        if panel_open is not None:
            if action == "open_repository":
                panel_open[0] = True
            elif action in ("open_deep_search", "open_detail", "close_panel", "show_error"):
                panel_open[0] = False
        total = fe_payload.get("total", "—")
        await send_json({"type": "tool_call", **fe_payload})
        await log_ws("nav", "TOOL",
                     f"Panel: action={action} total={total}")

    # ── Inject context into Gemini Live ───────────────────────────────────
    # BUG-B-FIX: llm_context kosong (edge case tool error internal) → placeholder
    # harus tetap ditutup agar Gemini tidak stuck dalam waiting state selamanya.
    if not llm_context:
        await _close_placeholder()
    if llm_context:
        # LANGUAGE FIX: Context messages are injected as "user" turns, so Gemini mirrors
        # their language when generating the response. Since context is always written in
        # Indonesian (for legal terminology reasons), this caused Gemini to respond in ID
        # even when the user spoke EN. Fix: append an explicit language instruction so
        # Gemini always responds in the language the human user actually spoke.
        _lang_suffix = (
            "\n\nIMPORTANT: The user spoke in English. Your response MUST be in English."
            if _user_lang == "en"
            else "\n\nPenting: Pengguna berbicara dalam Bahasa Indonesia. Jawab dalam Bahasa Indonesia."
        )

        # v29 Opsi 3: Format context_msg sebagai natural language eksplisit.
        if tool_name in ("get_data", "show_repository"):
            total   = fe_payload.get("total", 0) if fe_payload else 0
            q_label = tool_args.get("query", "permintaan pengguna")
            data_section = llm_context[:settings.RAG_CONTEXT_MAX_CHARS].strip()
            if total > 0:
                context_msg = (
                    f"Hasil pencarian database Legalitik untuk \"{q_label}\":\n"
                    f"Ditemukan {total} regulasi relevan.\n\n"
                    f"{data_section}\n\n"
                    f"Berdasarkan data di atas, jawab pertanyaan pengguna sekarang "
                    f"dalam 1-2 kalimat. Sebutkan nama regulasi yang paling relevan."
                    f"{_lang_suffix}"
                )
            else:
                context_msg = llm_context + _lang_suffix
        elif tool_name == "fetch_news":
            raw_ctx   = llm_context.strip()
            sentences = [s.strip() for s in raw_ctx.split("\n") if s.strip()]
            max_items = settings.NEWS_VOICE_CONTEXT_MAX_SENTENCES
            header    = sentences[0] if sentences else ""
            items     = [s for s in sentences[1:] if re.match(r"^\d+\.", s)][:max_items]
            items_str = "\n".join(items) if items else raw_ctx[:300]
            context_msg = (
                f"Berita hukum dan keuangan terbaru:\n"
                f"{header}\n"
                f"{items_str}\n\n"
                f"Sampaikan 1 berita utama kepada pengguna dalam 2 kalimat singkat. "
                f"Sebutkan sumber dan tanggalnya."
                f"{_lang_suffix}"
            )
        elif tool_name in ("deep_search", "detail"):
            context_msg = (
                f"Dokumen berhasil dibuka: {llm_context[:800]}\n\n"
                f"Sampaikan judul dokumen ini kepada pengguna secara singkat."
                f"{_lang_suffix}"
            )
        else:
            context_msg = llm_context[:2000] + _lang_suffix

        # v29 Opsi 2+3: Kirim context_msg sebagai turn baru eksplisit.
        # Tidak ada hold gate → tidak ada placeholder → langsung session.send.
        # Gemini mungkin sudah bersuara dari stale context sebelumnya.
        # Context turn baru ini akan menjadi referensi untuk pertanyaan lanjutan.
        # BUG-C: explicit answer trigger di akhir context_msg memastikan Gemini
        # merespons meski tidak ada audio baru dari user.
        _placeholder_sent = False
        if rag_in_flight is not None:
            rag_in_flight[0] = False
        try:
            await session.send(
                input=types.LiveClientContent(
                    turns=[types.Content(
                        role="user",
                        parts=[types.Part(text=context_msg)]
                    )],
                    turn_complete=True,
                )
            )
            await log_ws("ok", "CTX",
                         f"Context injected: {len(llm_context)} chars, tool={tool_name}")
        except Exception as e:
            await log_ws("warn", "CTX", f"Context injection failed: {e}")


def _prefetch_get_data(query: str, query_en: str | None = None) -> dict | None:
    """
    BUG-FIX #8: Accept query_en for bilingual prefetch.
    Previously only query_id was passed, so the prefetch result never matched
    the bilingual call in execute_tool (cache miss every time).
    """
    try:
        from app.rag_tools import get_data as _get_data
        return _get_data(
            query=query,
            query_en=query_en or None,
            top_k=settings.TOP_K_DOCS,
            voice_only=True,
        )
    except Exception as e:
        log.warning("ES prefetch failed: %s", e)
        return None


def _make_doc_hint(total: int, docs: list, tool_args: dict, open_panel: bool) -> str:
    """
    BUG #6: THIS FUNCTION IS DEAD CODE — it is never called anywhere.
    Context messages are built by _make_voice_context() in function_calling.py.
    Kept here for reference only. DO NOT add new calls to this function.
    If you need to change the LLM hint format, edit _make_voice_context() instead.
    """
    query  = tool_args.get("query", "")
    titles = [d.get("title") or d.get("kind2", "")
              for d in docs[:3] if isinstance(d, dict)]
    titles = [t for t in titles if t]

    if total == 0:
        return (
            f"[NO DOCUMENTS found in database for query '{query}'. "
            f"MUST inform the user that nothing was found. "
            f"STRICTLY PROHIBITED to fabricate regulation numbers, article content, or "
            f"legal information from internal knowledge. "
            f"Say: 'I'm sorry, I couldn't find [topic] in our database.']"
        )

    if not open_panel:
        if not titles:
            return (
                f"[Found {total} regulations related to '{query}'. "
                f"PANEL NOT OPEN — answer via voice ONLY. "
                f"DO NOT say 'already shown', 'already opened', or 'visible on screen'. "
                f"Mention the count in 1 sentence only.]"
            )
        title_list = ", ".join(titles[:2])
        suffix     = f" and {total - 2} others" if total > 2 else ""
        return (
            f"[Found {total} regulations: {title_list}{suffix}. "
            f"PANEL NOT OPEN — answer via voice only. "
            f"DO NOT claim the panel/repository is open or displayed. "
            f"Maximum 1 brief sentence.]"
        )
    else:
        if not titles:
            return (
                f"[Found {total} regulations. "
                f"Repository is now displayed on screen. Summarize briefly.]"
            )
        title_list = ", ".join(titles[:2])
        suffix     = f" and {total - 2} others" if total > 2 else ""
        return (
            f"[Found {total} regulations related to '{query}': "
            f"{title_list}{suffix}. "
            f"Repository is displayed. Mention count and 1-2 titles briefly.]"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Memory consolidation (v20-E: skip if < CONSOLIDATE_MIN_TURNS)
# ══════════════════════════════════════════════════════════════════════════════
def _start_consolidation(mem: MemorySystem) -> None:
    """v23: Uses shared Vertex AI client — no api_key parameter needed."""
    global _consolidation_done
    _consolidation_done = False

    def _run():
        global _consolidation_done
        session_turns = len(mem._session_turns) if hasattr(mem, "_session_turns") else 99
        if session_turns < settings.CONSOLIDATE_MIN_TURNS:
            log.info(
                "Consolidation skipped — session_turns=%d < min=%d",
                session_turns, settings.CONSOLIDATE_MIN_TURNS,
            )
            mem.reset_stm()
            _consolidation_done = True
            return
        mem.consolidate(client=_vertex_client)
        _consolidation_done = True

    threading.Thread(target=_run, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════
def start_server():
    preload_newest_cache()

    if settings.USE_NGROK and settings.NGROK_AUTH_TOKEN:
        try:
            from pyngrok import ngrok
            os.system("pkill -f ngrok 2>/dev/null || true")
            time.sleep(1)
            ngrok.set_auth_token(settings.NGROK_AUTH_TOKEN)
            tunnel     = ngrok.connect(settings.PORT, bind_tls=True)
            public_url = tunnel.public_url
            print("\n" + "═" * 72)
            print(f"  🌐  URL  →  {public_url}")
            print(f"  🔌  WS   →  {public_url.replace('https://','wss:/')}/ws")
            print("═" * 72 + "\n")
        except ImportError:
            print("pyngrok not installed — running without tunnel")

    _print_banner()
    uvicorn.run(
        "app.main:app",
        host      = "0.0.0.0",
        port      = settings.PORT,
        log_level = "warning",
        reload    = False,
    )


def _print_banner():
    s         = memory.stats
    local_url = f"http://localhost:{settings.PORT}"
    end_label = "HIGH" if settings.VAD_END_SENSITIVITY_HIGH else "LOW"
    print(
        "\n"
        "====================================================================\n"
        "  Legalitik Voice Agent v23.0\n"
        "====================================================================\n"
        f"  Auth        : Vertex AI (service account)\n"
        f"  Project     : {settings.VERTEX_PROJECT}\n"
        f"  Location    : {settings.VERTEX_LOCATION}\n"
        f"  Key file    : {settings.VERTEX_KEY_PATH}\n"
        "--------------------------------------------------------------------\n"
        f"  Live Model  : {settings.LIVE_MODEL}\n"
        f"  Flash Model : {settings.FLASH_MODEL}\n"
        f"  Port        : {settings.PORT}\n"
        "--------------------------------------------------------------------\n"
        f"  Memory file : {settings.MEMORY_FILE}\n"
        f"  Memory      : facts={s['facts']} prefs={s['preferences']} "
        f"user='{s['user_name']}'\n"
        "--------------------------------------------------------------------\n"
        f"  VAD         : silence={settings.VAD_SILENCE_DURATION_MS}ms "
        f"end={end_label} start={'LOW' if settings.VAD_START_SENSITIVITY_LOW else 'HIGH'}\n"
        "--------------------------------------------------------------------\n"
        "  v25.1 bug fixes (deep analysis — round 2):\n"
        "    [BUG-X1] STM carry regex: \\w → \\w+ so full words match (was broken)\n"
        "    [BUG-X2] Topic unlock: acronym-aware expansion prevents false unlocks\n"
        "    [BUG-A] CancelledError: _close_placeholder() on asyncio cancel\n"
        "    [BUG-B] Empty llm_context: placeholder now closed even if context empty\n"
        "    [BUG-C] TTS-safe close: _close_placeholder uses whitespace-only, no speech\n"
        "    [BUG-E] Topic unlock: auto-unlock when new RAG topic has no token overlap\n"
        "  v24 fixes retained: FIX-1..7 (triage, doc-id, context-blindness, weak-query)\n"
        "  v23 retained: Vertex AI, English default, shared client\n"
        "  v22.5 retained: nav_just_happened, turn_complete=True, anaphoric numbered results\n"
        "====================================================================\n"
        f"  UI   →  {local_url}\n"
        f"  Docs →  {local_url}/api/docs\n"
        "====================================================================\n"
    )


if __name__ == "__main__":
    start_server()