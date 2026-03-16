"""
classifier.py — Intent classifier v29.0

v26.0: LOG-3-FIX — _STOP_WORDS + fast_classify highlight noise filter.
v25.1: BUG-X1-FIX — _STM_CONTEXT_CARRY_RE: one-char match upgraded to full-word match.
  BUG-X2-FIX — topic_unlock: acronym & bilingual expansion awareness prevents false-positive unlock.
v25.0: BUG-D-FIX — _normalize_spoken_numbers single-pass, preserves case.
All v24.0 fixes retained.

v24.0 Fixes vs v23.0:
  FIX-1 (Loophole 5 — Anaphoric vs Chitchat):
    _ANAPHORIC_RE diperluas dengan frasa natural Bahasa Indonesia seperti
    "yang barusan dibahas", "yang tadi kita bicara", "yang itu tadi", dsb.
    Layer-0 chitchat fast-path sekarang melewati has_anaphoric_extended().

  FIX-2 (Loophole 7 — Specific Doc Identifier):
    _IS_DOC_ID_RE diperluas agar menangani:
      - Nomor yang ditulis dalam kata-kata "tujuh belas" → 17
      - Format "PBI 17" (tanpa kata "nomor")
      - Format "Peraturan BI 17"
    Fungsi has_doc_identifier() ditambahkan untuk pre-check sebelum Flash.

  FIX-3 (Loophole 2 — Context Blindness / STM Reset terlalu agresif):
    has_stm_followup() diperluas: mengenali pola "what about its X",
    "bagaimana dengan X-nya", "lanjut ke X" sebagai STM follow-up
    daripada chitchat.
    _PURE_CHITCHAT_RE diperketat agar tidak menangkap kalimat ambiguous
    seperti "Okay, then..." sebagai chitchat.

  Semua logic v23.0 dipertahankan.
"""
import json
import logging
import re
import time as _time

from google.genai import types

from app.config import settings
from app.prompts import FLASH_CLASSIFIER_PROMPT
from app.vertex import get_cached_client

log = logging.getLogger(__name__)

VALID_INTENTS = {
    "regulation_query", "open_repository", "preview_document",
    "detail_document", "highlight_keywords", "back_to_repository",
    "close_panel", "fetch_news", "chitchat",
}

_NAV_ONLY_INTENTS = {"back_to_repository", "close_panel", "highlight_keywords"}
UI_OPEN_INTENTS   = {"open_repository", "preview_document", "detail_document"}
_RAG_INTENTS: frozenset[str] = frozenset({
    "open_repository", "regulation_query", "fetch_news", "preview_document",
})

# ── ASR Normalizer (v22.5 unchanged) ─────────────────────────────────────────

_ASR_COMPOUNDS = [
    (re.compile(r"\bre\s+gula\s+tions?\b", re.I), "regulations"),
    (re.compile(r"\bregu\s+la\s+tions?\b", re.I), "regulations"),
    (re.compile(r"\bregu\s+lations?\b", re.I), "regulations"),
    (re.compile(r"\bregula\s+tions?\b", re.I), "regulations"),
    (re.compile(r"\bgu\s+la\s+tions?\b", re.I), "gulations"),
    (re.compile(r"\bgula\s+tions?\b", re.I), "gulations"),
    (re.compile(r"\bfi\s+nd\b", re.I), "find"),
    (re.compile(r"\bfin\s+d\b", re.I), "find"),
    (re.compile(r"\bf\s+ind\b", re.I), "find"),
    (re.compile(r"\bNo\s+w\b", re.I), "Now"),
    (re.compile(r"\bno\s+w\b", re.I), "now"),
    (re.compile(r"\bO\s+ke\b", re.I), "Oke"),
    (re.compile(r"\bO\s+kay\b", re.I), "Okay"),
    (re.compile(r"\bgoo\s+d\b", re.I), "good"),
    (re.compile(r"\bBan\s+k\b", re.I), "Bank"),
    (re.compile(r"\bban\s+k\b", re.I), "bank"),
    (re.compile(r"\bla\s+test\b", re.I), "latest"),
    (re.compile(r"\blat\s+est\b", re.I), "latest"),
    (re.compile(r"\bne\s+ws\b", re.I), "news"),
    (re.compile(r"\bn\s+ews?\b", re.I), "news"),
    (re.compile(r"\babo\s+ut\b", re.I), "about"),
    (re.compile(r"\bab\s+out\b", re.I), "about"),
    (re.compile(r"\bple\s+ase\b", re.I), "please"),
    (re.compile(r"\bplea\s+se\b", re.I), "please"),
    (re.compile(r"\ble\s+gal\b", re.I), "legal"),
    (re.compile(r"\bleg\s+al\b", re.I), "legal"),
    (re.compile(r"\bdo\s+cument\b", re.I), "document"),
    (re.compile(r"\bdoc\s+ument\b", re.I), "document"),
    (re.compile(r"\bco\s+uld\b", re.I), "could"),
    (re.compile(r"\bwou\s+ld\b", re.I), "would"),
    (re.compile(r"\bshou\s+ld\b", re.I), "should"),
    (re.compile(r"\bpre\s+view\b", re.I), "preview"),
    (re.compile(r"\bkey\s+word\b", re.I), "keyword"),
    (re.compile(r"\bdo\s+cu\s+ment\b", re.I), "document"),
    (re.compile(r"\bdocu\s+ment\b", re.I), "document"),
    (re.compile(r"\bre\s+po\s+si\s+to\s+ri\b", re.I), "repositori"),
    (re.compile(r"\breposi\s+tori\b", re.I), "repositori"),
    (re.compile(r"\bre\s+posi\s+tory\b", re.I), "repository"),
    (re.compile(r"\brepo\s+si\s+tory\b", re.I), "repository"),
    (re.compile(r"\breposito\s+ry\b", re.I), "repository"),
    (re.compile(r"\breposi\s+tory\b", re.I), "repository"),
    (re.compile(r"\bkeu\s+angan\b", re.I), "keuangan"),
    (re.compile(r"\bpe\s+ra\s+tu\s+ran\b", re.I), "peraturan"),
    (re.compile(r"\bpera\s+tura\b", re.I), "peraturan"),
    (re.compile(r"\bper\s+atu\s+ran\b", re.I), "peraturan"),
    (re.compile(r"\bperatu\s+ran\b", re.I), "peraturan"),
    (re.compile(r"\brela\s+ted\b", re.I), "related"),
    (re.compile(r"\bdocu\s+ments?\b", re.I), "document"),
    (re.compile(r"\bregu\s+la\s+tion\b", re.I), "regulation"),
    (re.compile(r"\bregula\s+tions?\b", re.I), "regulation"),
    (re.compile(r"\bIndo\s+nesia\b", re.I), "Indonesia"),
    (re.compile(r"\bclo\s+se\b", re.I), "close"),
    (re.compile(r"\bhigh\s+light\b", re.I), "highlight"),
    (re.compile(r"\banggo\s+ta\b", re.I), "anggota"),
    (re.compile(r"\bgu\s+ber\s+nur\b", re.I), "gubernur"),
    (re.compile(r"\bGu\s+bur\s+ne\b", re.I), "Gubernur"),
    (re.compile(r"\bKu\s+bur\s+ne\b", re.I), "Gubernur"),
    (re.compile(r"\bde\s+wan\b", re.I), "dewan"),
    (re.compile(r"\bno\s+mor\b", re.I), "nomor"),
    (re.compile(r"\bnomo\s+r\b", re.I), "nomor"),
    (re.compile(r"\bta\s+hun\b", re.I), "tahun"),
    (re.compile(r"\bo\s*j\s*k\b", re.I), "OJK"),
    (re.compile(r"\bl\s*p\s*s\b", re.I), "LPS"),
    (re.compile(r"\bp\s+a\s+d\s+g\b", re.I), "PADG"),
    (re.compile(r"\bp\s+p\b", re.I), "PP"),
    (re.compile(r"\bu\s+u\b", re.I), "UU"),
    (re.compile(r"\bp\s+e\s+r\s+p\s+u\b", re.I), "PERPU"),
    (re.compile(r"\bmacro\s+prudential\b", re.I), "macroprudential"),
    (re.compile(r"\bmakro\s+prudensial\b", re.I), "makroprudensial"),
    (re.compile(r"\bpru\s+den\s+sial\b", re.I), "prudensial"),
    (re.compile(r"\bke\s+bi\s+ja\s+kan\b", re.I), "kebijakan"),
    (re.compile(r"\bkebi\s+jakan\b", re.I), "kebijakan"),
    (re.compile(r"\bin\s+sen\s+tif\b", re.I), "insentif"),
    (re.compile(r"\bli\s+ku\s+idi\s+tas\b", re.I), "likuiditas"),
    (re.compile(r"\bpem\s+ben\s+tu\s+kan\b", re.I), "pembentukan"),
    (re.compile(r"\bpin\s+jaman\b", re.I), "pinjaman"),
    (re.compile(r"\bke\s+waji\s+ban\b", re.I), "kewajiban"),
    (re.compile(r"\bpeng\s+awas\s+an\b", re.I), "pengawasan"),
]

_YEAR_RE    = re.compile(r"\b(19|20)\d{2}\b")
_DOC_NUM_RE = re.compile(r"\b(no|nomor|no\.)\s*\d+\b", re.I)

_COMMON_SHORT_WORDS = frozenset({
    "a","me","my","or","of","on","to","in","at","by","us","up","go","no","so",
    "be","an","as","am","is","it","if","ok","hi","he","we","ya","the","and",
    "for","are","but","was","has","had","her","his","him","our","out","all",
    "any","two","too","how","who","why","now","can","you","let","top","get",
    "put","set","use","may","own","new","few","big","old","one","off","try",
    "ask","ago","yet","per","via","nor","due","far","lot","bit","say",
    "di","ke","ku","mu","si","itu","ada","apa","ini","oke","hey","dan","dari",
    "saja","iya","jadi","tapi","bisa","mau","ayo","ber","ter",
    "ple","ase","fi","nd","ne","ws","bo","ut","la","le","se","st","es",
    "d","w","k","re","na","nce","ed","al","ow",
})

_ASR_PRE_COMPOUNDS = [
    (re.compile(r"\bdo\s+cu\s+ment\b", re.I), "document"),
    (re.compile(r"\binforma\s+tion\b", re.I), "information"),
    (re.compile(r"\bregula\s+tion\b", re.I), "regulation"),
    (re.compile(r"\breposi\s+tori\b", re.I), "repositori"),
    (re.compile(r"\brepo\s+si\s+tory\b", re.I), "repository"),
    (re.compile(r"\breposi\s+tory\b", re.I), "repository"),
    (re.compile(r"\bre\s+po\s+si\s+to\s+ry\b", re.I), "repository"),
    (re.compile(r"\bdoku\s+men\b", re.I), "dokumen"),
]
_ASR_POST_COMPOUNDS = [
    (re.compile(r"\bdocu\s*ment\b", re.I), "document"),
    (re.compile(r"\binforma\s*tion\b", re.I), "information"),
    (re.compile(r"\breposi\s*tori\b", re.I), "repositori"),
    (re.compile(r"\breposi\s*tory\b", re.I), "repository"),
    (re.compile(r"\bre\s+pository\b", re.I), "repository"),
    (re.compile(r"\brepo\s+sitory\b", re.I), "repository"),
]

# ── FIX-2: Spoken number → digit map ────────────────────────────────────────
# Menangkap kasus "Peraturan BI nomor tujuh belas" → "Peraturan BI nomor 17"
_SPOKEN_NUMBERS: dict[str, str] = {
    "satu": "1", "dua": "2", "tiga": "3", "empat": "4", "lima": "5",
    "enam": "6", "tujuh": "7", "delapan": "8", "sembilan": "9", "sepuluh": "10",
    "sebelas": "11", "dua belas": "12", "tiga belas": "13", "empat belas": "14",
    "lima belas": "15", "enam belas": "16", "tujuh belas": "17",
    "delapan belas": "18", "sembilan belas": "19", "dua puluh": "20",
    "dua puluh satu": "21", "dua puluh dua": "22", "dua puluh tiga": "23",
    "tiga puluh": "30", "empat puluh": "40", "lima puluh": "50",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
}


def _normalize_spoken_numbers(text: str) -> str:
    """
    FIX-2 / BUG-D-FIX: Ubah angka yang diucapkan menjadi digit.
    "tujuh belas" → "17", "nomor tujuh belas" → "nomor 17"

    BUG-D yang diperbaiki:
      v24 punya DUA masalah:
        1. s = text.lower() di baris pertama → "PBI" jadi "pbi", merusak kapitalisasi.
        2. Loop ganda: loop pertama lowercase, loop kedua case-insensitive tapi
           sudah di-lowercase → redundan dan confusing.
      Fix: satu pass, case-insensitive, TIDAK mengubah kapitalisasi teks lainnya.
    """
    result = text
    # Single pass, longest-first (prevent "dua puluh" matching "dua" first),
    # case-insensitive, PRESERVES original case of surrounding text
    for spoken, digit in sorted(_SPOKEN_NUMBERS.items(), key=lambda x: -len(x[0])):
        result = re.sub(r'\b' + re.escape(spoken) + r'\b', digit, result, flags=re.I)
    return result


def _asr_normalize(text: str) -> str:
    s = text
    for p, r in _ASR_COMPOUNDS:
        s = p.sub(r, s)
    for p, r in _ASR_PRE_COMPOUNDS:
        s = p.sub(r, s)
    for _ in range(4):
        prev = s
        s = re.sub(r'\b(\d)\s+(\d)\s+(\d)\s+(\d)\b', r'\1\2\3\4', s)
        s = re.sub(r'\b(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\b', r'\1\2\3', s)
        s = re.sub(r'\b(\d{1,3})\s+(\d{1,3})\b', r'\1\2', s)
        if s == prev:
            break
    tokens, changed, passes = s.split(), True, 0
    while changed and passes < 5:
        passes += 1; changed = False; result = []; i = 0
        while i < len(tokens):
            tok = tokens[i]; nt = tokens[i+1] if i+1 < len(tokens) else ""
            if (1 <= len(tok) <= 3 and tok.isalpha()
                    and tok.lower() not in _COMMON_SHORT_WORDS
                    and not tok[0].isupper() and i+1 < len(tokens)
                    and nt.isalpha() and not nt[0].isupper()):
                result.append(tok+nt); i += 2; changed = True
            else:
                result.append(tok); i += 1
        tokens = result
    s = " ".join(tokens)
    for p, r in _ASR_POST_COMPOUNDS:
        s = p.sub(r, s)
    # FIX-2: Normalisasi angka yang diucapkan
    s = _normalize_spoken_numbers(s)
    return s


# ── Layer-0 detectors ─────────────────────────────────────────────────────────

_NEEDS_FLASH_RE = re.compile(
    r"\b(open|find|search|show|preview|detail|highlight|sorot|mark|"
    r"looking\s+for|look\s+for|help\s+me|"
    r"cari|buka|tampilkan|tunjukkan|carikan|"
    r"document|doc\b|regulation|rule|law|peraturan|regulasi|dokumen|aturan|"
    r"repositor|pojk|pbi|padg|pp\b|uu\b|perpu|permen|"
    r"berita|news|update|terbaru|kabar|latest|recent|"
    r"related\s+to|terkait|tentang|"
    r"finance|keuangan|hukum|mikro|makro|prudential|prudensial|"
    r"back|kembali|close|tutup|"
    r"apa\s+itu|jelaskan|explain|what\s+is|berapa|pasal|bab|ayat|"
    r"who\s+is|who\s+are|who\s+was|who\s+were|whose\b|"
    r"siapa\b|siapakah\b|where\s+is|when\s+is|when\s+was|"
    r"how\s+many|how\s+much|how\s+does|how\s+do\b|"
    r"how\s+about|what\s+about|"
    r"highlight|keyword|kata\s+kunci|"
    r"it.?s\s+about|about\s+the\s+\w|the\s+document\s+is|"
    r"i\s+mean\s+\w|regarding\s+\w|yang\s+tentang|maksudnya|soal\s+\w"
    r")\b", re.I,
)

# FIX-1: _ANAPHORIC_RE diperluas dengan frasa natural Bahasa Indonesia
_ANAPHORIC_RE = re.compile(
    r"(\btop\s+\d+\s+of\b|\bfirst\s+\d+\s+of\b|\blast\s+\d+\s+of\b|"
    r"\b(top|first|second|third|last)\s+(one|two|three|four|five)\b|"
    r"\byang\s+(pertama|kedua|ketiga|terakhir|tadi|tersebut|nomor)\b|"
    r"\btersebut\b|\byang\s+tadi\b|\byang\s+terakhir\b|"
    r"\bnomor\s+\d+\s*(nya|tadi)?\b|"
    r"\bsebutkan\s+(top|yang|mereka|itu)\b|"
    r"\bbacakan\s+(top|yang|mereka|itu|nomor)\b|"
    r"\bringkaskan\s+(itu|mereka|tersebut)\b|"
    r"\bsummarize\s+(them|those|it)\b|"
    r"\btell\s+me\s+(top|more\s+about\s+them|about\s+them)\b|"
    r"\b(of\s+them|of\s+those)\b|"
    r"\bwhat\s+is\s+the\s+answer\b|"
    r"\bapa\s+(jawaban|hasilnya|isinya|intinya)\b|"
    r"\bapa\s+yang\s+(dimaksud|terjadi|ada)\b|"
    # FIX-1: Tambahan frasa natural Bahasa Indonesia
    r"\byang\s+barusan\s+(dibahas|disebutkan|dicari|dibuka|ditemukan)\b|"
    r"\byang\s+tadi\s+(kita|kamu|anda)?\s*(bahas|bicara|cari|sebut|buka)\b|"
    r"\byang\s+itu\s+tadi\b|\byang\s+kamu\s+temukan\b|"
    r"\byang\s+kita\s+cari\b|\byang\s+baru\s+saja\b|"
    r"\b(lanjut|lanjutkan)\s+(ke|dengan|dari)\s+\w|"
    r"\bitu\s+tadi\b|\bmereka\s+tadi\b|"
    r"\bthe\s+one\s+(you|we)\s+(found|mentioned|searched|showed)\b|"
    r"\bwhat\s+(you|we)\s+(just|were)\s+(found|mentioned|showed|searched)\b|"
    r"\bthe\s+previous\s+(one|result|document|regulation)\b|"
    r"\bthe\s+last\s+(one|result|document)\b)"
    , re.I,
)

_STM_CLARIFY_RE = re.compile(
    r"\b(it.?s\s+about|it\s+is\s+about|about\s+the\s+\w|"
    r"the\s+document\s+is|the\s+doc\s+is|i\s+mean\s+\w|"
    r"regarding\s+\w|yang\s+tentang\s+\w|maksudnya\s+\w|soal\s+\w)\b", re.I,
)

# FIX-3: _PURE_CHITCHAT_RE diperketat — hindari menangkap kalimat transisi ambiguous.
# "Okay, then..." / "Alright, let's..." → BUKAN pure chitchat jika diikuti klausa.
# Aturan baru: hanya cocokkan jika kalimat BERAKHIR setelah kata filler (tidak ada kelanjutan
# dengan kata benda/kerja yang bermakna).
_PURE_CHITCHAT_RE = re.compile(
    r"^((hi|hello|hey|halo|hei)\b[^a-z]*$|"   # greeting tanpa kelanjutan
    r"do\s+you\s+(hear|understand|know|remember)\b.*|"
    r"can\s+you\s+(hear|tell)\b.*|"            # "can you help" DIHAPUS — terlalu luas
    r"are\s+you\s+(there|ready|ok)\b.*|"
    r"(good|nice)\s+(morning|afternoon|evening|day)\b.*|"
    r"thank(s|\s+you)\b.*|"
    r"(okay|ok|yes|no|yeah|yep|nope|alright|sure)\s*[.,!?]*\s*$|"   # FIX-3: harus akhir kalimat
    r"(apa\s+kabar|halo|hai|selamat)\b[^a-z]*$)$",  # greeting murni
    re.I,
)

_OPEN_SENTENCE_START_RE = re.compile(
    r"^(can|could|would|will|please|may|do|does|did|have|has|is|are|was|were"
    r"|bisakah|boleh|apakah|dapatkah|tolong|coba)\b", re.I,
)

# FIX-3: Pola STM follow-up tambahan untuk menangkap kalimat transisi yang
# sebenarnya mengacu ke topik RAG sebelumnya.
#
# BUG-X1-FIX: r"\w" (1 karakter) diganti r"\w+" (satu kata penuh) sebelum trailing r"\b".
# Sebelumnya "what about its impact?" tidak match karena r"\w" hanya menangkap "i",
# lalu trailing r"\b" gagal karena karakter berikutnya "m" adalah word-char.
# Dengan r"\w+" pola menangkap kata penuh "impact" dan r"\b" match di akhir kata.
_STM_CONTEXT_CARRY_RE = re.compile(
    r"\b(what\s+about\s+(its?|their|the)\s+\w+|"
    r"how\s+about\s+(its?|the)\s+\w+|"
    r"bagaimana\s+dengan\s+\w+|"
    r"dan\s+bagaimana\s+(dengan|tentang)\s+\w+|"
    r"lalu\s+(apa|bagaimana)\s+\w+|"
    r"terus\s+(apa|bagaimana)\s+\w+|"
    r"kalau\s+(itu|ini)\s+\w+|"
    r"what\s+about\s+that|"
    r"and\s+what\s+(about|if)\b|"
    r"okay\s+(then|so)\s+(what|how|can|could|find|search|cari|tell)\b)\b", re.I,
)


def needs_flash(text: str) -> bool:
    return bool(_NEEDS_FLASH_RE.search(text)) or bool(_YEAR_RE.search(text)) or bool(_DOC_NUM_RE.search(text))


def has_anaphoric(text: str) -> bool:
    return settings.ANAPHORIC_FLASH and bool(_ANAPHORIC_RE.search(text))


def has_stm_followup(text: str, last_intent: str) -> bool:
    if last_intent not in _RAG_INTENTS:
        return False
    normalized = _asr_normalize(text)
    words = normalized.split()
    n = len(words)
    if n > settings.STM_FOLLOWUP_MAX_WORDS:
        return False
    for candidate in (text.strip().rstrip(".,!?"), normalized.strip().rstrip(".,!?")):
        if _PURE_CHITCHAT_RE.match(candidate):
            return False
    if _OPEN_SENTENCE_START_RE.match(normalized.strip()):
        return False
    if n == 1 and len(normalized.strip()) <= 4:
        return False
    if _STM_CLARIFY_RE.search(text) or _STM_CLARIFY_RE.search(normalized):
        return True
    # FIX-3: Tambahan context-carry patterns
    if _STM_CONTEXT_CARRY_RE.search(text) or _STM_CONTEXT_CARRY_RE.search(normalized):
        return True
    return 2 <= n <= 3


# ── FIX-2: Doc identifier checker yang lebih luas ─────────────────────────────
# Menangani: "PBI 17", "Peraturan BI 17", "nomor tujuh belas" (setelah normalisasi)
_IS_DOC_ID_RE_EXTENDED = re.compile(
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


def has_doc_identifier(text: str) -> bool:
    """
    FIX-2: Cek apakah teks mengandung identifikasi dokumen yang spesifik.
    Digunakan di intent_to_tool_args untuk menentukan apakah harus direct deep_search
    atau ES lookup dulu.
    """
    normalized = _asr_normalize(text)
    return bool(_IS_DOC_ID_RE_EXTENDED.search(text)) or bool(_IS_DOC_ID_RE_EXTENDED.search(normalized))


# ── Layer-1 keyword rules ──────────────────────────────────────────────────────

_NEWS_GUARD_RE = re.compile(
    r"\b(berita|news|update\s+terbaru|latest\s+news|recent\s+news|"
    r"kabar\s+terbaru|info\s+terbaru|ada\s+berita|what.?s\s+new)\b", re.I,
)

_KEYWORD_RULES = [
    ("regulation_query", [
        r"\b(find|cari|carikan)\s+(me\s+)?(a\s+)?(regulations?|peraturan|aturan)\s+(like|similar\s+to|such\s+as|about|tentang|seperti|mirip|terkait)\b",
        r"\b(regulation|peraturan|aturan)\s+(about|tentang|terkait|mengenai)\b",
        r"\bapa\s+(itu|yang\s+dimaksud|saja|isi)\b",
        r"\bjelaskan\s+\w", r"\bexplain\s+\w", r"\bceritakan\s+tentang\b",
        r"\btell\s+me\s+(about|what|how)\b",
        r"\bwhat\s+is\s+\w", r"\bwhat\s+are\s+\w", r"\bhow\s+does\s+\w",
        r"\bberapa\b", r"\bapa\s+isi\b", r"\bdescribe\s+\w",
        r"\bwho\s+(is|are|was|were)\s+\w", r"\bwhose\s+\w",
        r"\bsiapa\s+(itu|yang|adalah|gubernur|ketua|direktur)\b", r"\bsiapakah\b",
    ]),
    ("fetch_news", [
        r"\bberita\s+terbaru\b",
        r"\bberita\s+(keuangan|hukum|regulasi|peraturan|finansial|ojk|lps|bi\b)",
        r"\bberita\s+(tentang|apa)\b", r"\bupdate\s+terbaru\b", r"\bada\s+berita\b",
        r"\binfo\s+terbaru\b", r"\bkabar\s+terbaru\b", r"\bkabar\s+apa\b",
        r"\bterbaru\s+tentang\b", r"\blatest\s+news\b", r"\brecent\s+news\b",
        r"\bnews\s+(about|of|on|from)\b", r"\bwhat.?s\s+new\s+in\b",
        r"\bcurrent\s+developments\b",
        r"\b(find|get|give|tell)\s+me\s+(the\s+)?(latest|recent|newest|current)\s+news\b",
        r"\b(cari|carikan)\s+berita\b", r"\bberita\s+tentang\b",
    ]),
    ("preview_document", [
        r"\bpreview\s+\w",
        r"\bbuka\s+(pbi|pojk|pp|uu|perpu|permen|padg)\b",
        r"\bopen\s+(pbi|pojk|pp|uu|perpu|permen|padg)\b",
        r"\bbuka\s+pp\s+(no|nomor)\b", r"\bopen\s+pp\s+(no|nomor)\b",
        r"\bperaturan\s+anggota\s+dewan\s+gubernur",
        r"\bpadg\s+(nomor|no|nomer)\b", r"\bpa\s+(nomor|no|nomer)\s+\d",
        r"\bbuka\s+peraturan\s+anggota", r"\bopen\s+peraturan\s+anggota",
    ]),
    ("open_repository", [
        r"\bopen\s+(?:the\s+)?repositor", r"\bbuka\s+(?:the\s+)?repositori",
        r"\bfind\s+(a\s+)?document\b", r"\bfind\s+(me\s+)?(a\s+)?document\b",
        r"\bfind\s+(a\s+)?regulation\b", r"\bhelp\s+me\s+(to\s+)?find\b",
        r"\blooking\s+for\s+(a\s+)?(document|regulation|peraturan|dokumen|regulasi)\b",
        r"\bsearch\s+(for\s+)?(a\s+)?(document|regulation|peraturan)\b",
        r"\bcari\s+(dokumen|regulasi)\b", r"\bcarikan\s+(dokumen|regulasi)\b",
        r"\bcarikan\s+tentang\b",
        r"\btampilkan\s+(dokumen|peraturan|regulasi)\b",
        r"\btampilkan\s+(?:the\s+)?repositor",
        r"\bshow\s+(me\s+)?(a\s+)?(document|regulation)\b",
        r"\bdocument\s+(about|related\s+to|terkait)\b",
        r"\blegal\s+document\b", r"\brelated\s+to\s+\w+",
    ]),
    ("back_to_repository", [
        r"\bback\s+to\s+repositor", r"\bgo\s+back\b",
        r"\bkembali\b", r"\breturn\s+to\s+(list|repositor)\b",
    ]),
    ("close_panel", [
        r"\bclose\s+(this|panel|page)\b",
        r"\bclose\s+the\s+(repositor|panel|document|doc|page)\b",
        r"\btutup\b", r"\bclose$", r"\bgo\s+home\b",
        r"\bexit\s+(the\s+)?(panel|repositor|document|doc)\b", r"\bkeluar\b",
    ]),
]

_PERSONAL_QUESTION_RE = re.compile(
    r"\b(what\s+is\s+your\s+\w|what\s+are\s+you\b|who\s+are\s+you\b|"
    r"who\s+made\s+you|who\s+created|what\s+can\s+you\s+do|"
    r"are\s+you\s+(an?\s+)?ai\b|your\s+name\b|"
    r"nama\s+kamu|nama\s+anda|siapa\s+nama\s+\w|"
    r"kamu\s+siapa\b|anda\s+siapa\b|kamu\s+itu\s+(apa|siapa)|"
    r"apa\s+yang\s+bisa\s+(kamu|anda))\b", re.I,
)

_SEARCH_VERB = re.compile(
    r"\b(find|search|look\s*(?:ing)?\s*for|show|browse|list|cari|temukan|tampilkan|carikan)\b", re.I)
_DOC_NOUN = re.compile(
    r"\b(document|doc\b|regulation|rule|law|peraturan|regulasi|dokumen|aturan)\b", re.I)
_NEWS_SIGNAL = re.compile(r"\b(berita|news|update|terbaru|kabar|latest|recent)\b", re.I)
_OPEN_VERB = re.compile(r"\b(buka|open|preview|lihat|tampilkan|show)\b", re.I)
_QUESTION_INTENT_RE = re.compile(
    r"\b(like\s+\w|similar\s+to|such\s+as|related\s+to\s+\w|about\s+\w|"
    r"what\s+(is|are|does)|apa\s+(itu|saja|yang)|jelaskan|explain|"
    r"tell\s+me\s+(about|what)|how\s+(does|do|is)|berapa|ceritakan|describe)\b", re.I)

_STOP_WORDS = {
    "open","find","search","show","help","me","to","a","the","please",
    "can","you","i","want","need","get","look","for","and","or","in",
    "on","at","of","is","it","why","cari","buka","tampilkan","tolong",
    "saya","mau","bisa","dan","dokumen","document","repository","repositori",
    "noise","where","how","what","okay","ok","yeah","yes","no","carikan",
    "good","great","fine","sure","well","just","also","like","but",
    "goo","wha","tha","oke","baik","halo","hai",
    # LOG-3-FIX: kata highlight/keyword harus difilter dari keyword extraction
    "highlight","keyword","sorot","mark","kata","kunci","word","kata-kunci",
    # v28+v29: tambah kata umum yang sering masuk sebagai keyword noise
    "thank","thanks","now","like","give","tell","want","need","sekarang",
    "nice","mean","well","right","sure","good","fine","yes","really",
}


def _keyword_classify(text, page, last_intent="", last_topic=""):
    if _PERSONAL_QUESTION_RE.search(text) or _PERSONAL_QUESTION_RE.search(_asr_normalize(text)):
        return None
    candidates = [text.lower(), _asr_normalize(text.lower())]
    for candidate in candidates:
        if page == "docpreview" and re.search(r"\b(highlight|find\s+word|cari\s+kata|sorot|mark)\b", candidate):
            return "highlight_keywords"
        has_news = bool(_NEWS_GUARD_RE.search(candidate))
        for intent, patterns in _KEYWORD_RULES:
            if intent == "open_repository" and has_news:
                continue
            for pat in patterns:
                if re.search(pat, candidate, re.I):
                    return intent
        if not has_news and _SEARCH_VERB.search(candidate) and _DOC_NOUN.search(candidate):
            return "regulation_query" if _QUESTION_INTENT_RE.search(candidate) else "open_repository"
        if _NEWS_SIGNAL.search(candidate) and not _DOC_NOUN.search(candidate):
            return "fetch_news"
    if last_intent in ("open_repository","regulation_query","preview_document") and last_topic:
        tl = text.lower().strip().rstrip(".,!?")
        if _OPEN_VERB.search(tl) and len(tl.split()) <= 4:
            return "preview_document"
    return None


def _extract_query(text, max_words=6):
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    words = [w for w in cleaned.lower().split()
             if w not in _STOP_WORDS and (w.isdigit() or len(w) > 2)]
    return " ".join(words[:max_words])


def _extract_full_query(text):
    return _extract_query(text, max_words=8)


def _extract_response_text(resp):
    try:
        if resp.text:
            return resp.text
    except Exception:
        pass
    try:
        candidates = getattr(resp, "candidates", None)
        if not candidates:
            return ""
        c = candidates[0]
        content = getattr(c, "content", None)
        if not content:
            return ""
        parts = getattr(content, "parts", None)
        if not parts:
            return ""
        return "".join(
            getattr(p, "text", "") for p in parts
            if p and not getattr(p, "thought", False) and getattr(p, "text", "")
        )
    except Exception:
        return ""


def _call_flash(client, model: str, prompt: str):
    last_err = None
    for attempt in range(settings.FLASH_MAX_RETRIES + 1):
        try:
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=512,
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(include_thoughts=False),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except Exception as e:
            last_err = e
            eu = str(e).upper()
            if any(x in eu for x in ("503","UNAVAILABLE","RESOURCE_EXHAUSTED","429","QUOTA")) \
                    and attempt < settings.FLASH_MAX_RETRIES:
                delay = settings.FLASH_RETRY_DELAY * (attempt + 1)
                log.warning("Flash retry %d in %.2fs: %s", attempt+1, delay, e)
                _time.sleep(delay)
                continue
            raise
    raise last_err


def fast_classify(text, page="home", last_intent="", last_topic=""):
    cleaned = re.sub(r"<[^>]+>\s*", "", text).strip()
    if not cleaned or cleaned in (".", ",", "stop", "okay stop"):
        return _make_fast_result("chitchat", cleaned, "empty/noise input")
    normalized = _asr_normalize(cleaned)
    if settings.FAST_CHITCHAT_SKIP_FLASH:
        if not needs_flash(cleaned) and not needs_flash(normalized):
            if has_anaphoric(cleaned) or has_anaphoric(normalized):
                return None
            if has_stm_followup(cleaned, last_intent) or has_stm_followup(normalized, last_intent):
                return None
            # FIX-3: Tambahan check context-carry sebelum chitchat fast-path
            if last_intent in _RAG_INTENTS and last_topic:
                if _STM_CONTEXT_CARRY_RE.search(cleaned) or _STM_CONTEXT_CARRY_RE.search(normalized):
                    return None  # Defer to Flash untuk context-carry
            return _make_fast_result("chitchat", cleaned, "Layer-0 fast-path")
    layer1 = _keyword_classify(normalized, page, last_intent, last_topic)
    if not layer1:
        layer1 = _keyword_classify(cleaned, page, last_intent, last_topic)
    if not layer1:
        return None
    if layer1 in _NAV_ONLY_INTENTS:
        sq = _extract_query(normalized)
        kw = sq.split()
        # LOG-3-FIX: untuk highlight_keywords, hapus noise words dari keyword list
        # sehingga "highlight the keyword Bank Umum Syariah" menghasilkan
        # kw=['Bank','Umum','Syariah'] bukan ['highlight','keyword',...]
        if layer1 == "highlight_keywords":
            _HL_FAST_NOISE = frozenset({
                "highlight","keyword","sorot","mark","the","word","kata","this",
                "page","in","di","pada","halaman","please","find","cari","ini",
                "sini","dokumen","document","and","dan","kunci","kata-kunci",
                # v28+v29: noise words yang sering lolos
                "like","now","okay","ok","thank","thanks","you","me","my",
                "please","just","for","a","an","to","of","that","these","those",
                "give","show","tell","want","need","can","could","would","should",
                "sekarang","tolong","aku","kamu","saya","minta","mau","bisa",
                # v29 BUG-D: tambah nice/mean/well/i mean
                "nice","mean","well","right","sure","good","great","fine",
                "yes","really","actually","basically","literally","exactly",
            })
            kw = [w for w in kw if w.lower() not in _HL_FAST_NOISE]
            sq = " ".join(kw)
        return _make_fast_result(layer1, normalized, "Layer-1 nav fast-path",
                                 search_query=sq, keywords=kw)
    sq = _extract_full_query(normalized)
    return _make_fast_result(layer1, normalized, "Layer-1 hint (Flash pending)",
                             search_query=sq, is_hint=True)


def _make_fast_result(intent, text, reason, search_query="", keywords=None, is_hint=False):
    kw = keywords or []
    return {"reconstructed": text, "intent": intent, "search_query": search_query,
            "document_name": "", "instansi": "", "jenis": "", "tahun": "",
            "keywords_en": kw, "keywords_id": kw, "keywords": kw,
            "reasoning": reason, "_is_hint": is_hint}


class FlashClassifier:
    def __init__(self, api_key: str | None = None, client=None):
        # v23: accept pre-built client or fall back to cached singleton
        self._client = client if client is not None else get_cached_client()
        self._model  = settings.FLASH_MODEL

    def classify(self, raw_fragments, current_page="home",
                 conversation_context="", last_intent="", last_topic=""):
        try:
            return self._classify_inner(raw_fragments, current_page,
                                        conversation_context, last_intent, last_topic)
        except Exception as e:
            log.exception("classify() outer-catch: %s", e)
            return self._default(" ".join(raw_fragments or []).strip(), f"outer-catch: {e}")

    def _classify_inner(self, raw_fragments, current_page, conversation_context,
                        last_intent, last_topic):
        raw     = " ".join(raw_fragments).strip()
        cleaned = re.sub(r"<[^>]+>\s*", "", raw).strip()
        if not cleaned or cleaned in (".", ",", "stop", "okay stop"):
            return self._default(raw)
        normalized = _asr_normalize(cleaned)

        if settings.FAST_CHITCHAT_SKIP_FLASH:
            if not needs_flash(cleaned) and not needs_flash(normalized):
                if not has_anaphoric(cleaned) and not has_anaphoric(normalized):
                    if not has_stm_followup(cleaned, last_intent) and \
                       not has_stm_followup(normalized, last_intent):
                        # FIX-3: Tambahan context-carry check di classify_inner juga
                        if not (last_intent in _RAG_INTENTS and last_topic and
                                (_STM_CONTEXT_CARRY_RE.search(cleaned) or
                                 _STM_CONTEXT_CARRY_RE.search(normalized))):
                            return self._make_chitchat(normalized)

        layer1_intent = _keyword_classify(normalized, current_page, last_intent, last_topic)
        if not layer1_intent:
            layer1_intent = _keyword_classify(cleaned, current_page, last_intent, last_topic)

        ctx = conversation_context.strip() if conversation_context else "(no prior conversation)"
        prompt = FLASH_CLASSIFIER_PROMPT.format(
            raw=normalized, page=current_page, conversation_context=ctx,
            last_intent=last_intent or "(none)", last_topic=last_topic or "(none)",
        )

        result = None; text = ""
        try:
            resp     = _call_flash(self._client, self._model, prompt)
            raw_text = _extract_response_text(resp)
            if not raw_text:
                raise ValueError("Empty Flash response")
            text   = raw_text.strip().replace("```json","").replace("```","").strip()
            result = json.loads(text)
            if not isinstance(result, dict):
                return (self._from_layer1(layer1_intent, normalized)
                        if layer1_intent else self._default(raw, "non-dict"))

            flash_intent = result.get("intent", "chitchat")
            if flash_intent not in VALID_INTENTS:
                flash_intent = "chitchat"

            reconstructed = result.get("reconstructed", normalized)
            l1_on_recon   = _keyword_classify(reconstructed, current_page, last_intent, last_topic)
            final_l1      = layer1_intent or l1_on_recon

            _L1_BEATS = {
                # BUG #1 FIX: open_repository must beat preview_document when Layer-1
                # detected "open repository" but Flash classified preview_document because
                # the sentence also contained a specific doc name.
                # Example: "open the repository and find PBI 7/2025" — Flash sees RULE 2
                # (open + doc name) and returns preview_document, but Layer-1 correctly
                # detected "open repository" via _EXPLICIT_OPEN_RE / keyword rules.
                # Layer-1 should win in this case.
                ("open_repository",    "preview_document"),
                ("open_repository",    "regulation_query"),
                ("preview_document",   "open_repository"),
                ("preview_document",   "regulation_query"),
                ("detail_document",    "open_repository"),
                ("detail_document",    "regulation_query"),
                ("highlight_keywords", "open_repository"),
                ("highlight_keywords", "regulation_query"),
                ("highlight_keywords", "chitchat"),
                ("back_to_repository", "open_repository"),
                ("close_panel",        "open_repository"),
            }
            if final_l1 and flash_intent == "chitchat":
                result["intent"] = final_l1
                result["reasoning"] = f"[L1 override chitchat] {result.get('reasoning','')}"
                if not result.get("search_query"):
                    sq = _extract_full_query(reconstructed)
                    result["search_query"] = sq
                    result.setdefault("keywords_en", sq.split())
                    result.setdefault("keywords_id", sq.split())
            elif final_l1 and (final_l1, flash_intent) in _L1_BEATS:
                result["intent"] = final_l1
                result["reasoning"] = f"[L1 priority] {result.get('reasoning','')}"
                if not result.get("search_query"):
                    sq = _extract_full_query(reconstructed)
                    result["search_query"] = sq
                    result.setdefault("keywords_en", sq.split())
                    result.setdefault("keywords_id", sq.split())
            else:
                result["intent"] = flash_intent

        except (json.JSONDecodeError, ValueError) as e:
            log.error("Flash parse error: %s | text=%r", e, text[:120])
            l1n = _keyword_classify(normalized, current_page, last_intent, last_topic)
            el1 = l1n or layer1_intent
            return (self._from_layer1(el1, normalized) if el1
                    else self._default(raw, str(e)))
        except Exception as e:
            log.error("Flash API error: %s", e)
            l1n = _keyword_classify(normalized, current_page, last_intent, last_topic)
            el1 = l1n or layer1_intent
            return (self._from_layer1(el1, normalized) if el1
                    else self._default(raw, str(e)))

        return self._postprocess(result, normalized, last_intent, last_topic)

    def _postprocess(self, result, normalized, last_intent, last_topic):
        intent = result.get("intent") or "chitchat"
        for k in ("reconstructed","search_query","document_name","instansi","jenis","tahun","reasoning"):
            if result.get(k) is None:
                result[k] = ""
        if intent in ("preview_document","detail_document"):
            dn = (result.get("document_name") or "").strip()
            yr = (result.get("tahun") or "").strip()
            if dn and yr and "tahun" not in dn.lower():
                result["document_name"] = f"{dn} Tahun {yr}"
            if dn and not yr:
                recon = result.get("reconstructed") or ""
                m = re.search(r'\btahun\s+(\d{4})\b', recon, re.I)
                if m and m.group(1) not in dn:
                    result["document_name"] = f"{dn} Tahun {m.group(1)}"
        if intent == "highlight_keywords":
            recon = result.get("reconstructed") or ""
            m = re.search(
                r"\b(?:highlight|sorot|mark|find\s+word|cari\s+kata)\s+"
                r"(?:the\s+word\s+|kata\s+|kalimat\s+)?(.+?)(?:\s+(?:in\s+this|in\s+the|di\s+(?:halaman|ini|dokumen|sini)|on\s+this)\b.*)?$",
                recon, re.I)
            if m:
                phrase = m.group(1).strip().rstrip(".,?!")
                if phrase:
                    result["keywords_id"] = result["keywords_en"] = result["keywords"] = [phrase]
            else:
                _HL_NOISE = frozenset({"highlight","sorot","mark","the","word","kata","this",
                    "page","in","di","pada","halaman","please","find","cari","ini","sini","dokumen","document","and","dan"})
                kw_id = [w for w in (result.get("keywords_id") or []) if w.lower() not in _HL_NOISE]
                if kw_id:
                    result["keywords_id"] = result["keywords"] = kw_id
        if (intent == "preview_document" and not result.get("document_name")
                and settings.STM_TOPIC_FALLBACK and last_topic):
            result["document_name"] = last_topic
            result["reasoning"] = f"[STM fill] {result.get('reasoning','')}"
        kw_en = result.get("keywords_en") or []; kw_id = result.get("keywords_id") or []
        result["keywords"] = list(dict.fromkeys(kw_id + kw_en))
        for k in ("reconstructed","search_query","document_name","instansi","jenis","tahun","reasoning"):
            result.setdefault(k, "")
            if result[k] is None:
                result[k] = ""
        result["intent"] = intent; result["_is_hint"] = False
        log.info("Classify → intent:%s sq:'%s' doc:'%s'",
                 intent, (result.get("search_query") or "")[:50],
                 (result.get("document_name") or "")[:40])
        return result

    @staticmethod
    def _make_chitchat(text):
        return {"reconstructed": text, "intent": "chitchat", "search_query": "",
                "document_name": "", "instansi": "", "jenis": "", "tahun": "",
                "keywords_en": [], "keywords_id": [], "keywords": [],
                "reasoning": "Layer-0 fast-path chitchat", "_is_hint": False}

    @staticmethod
    def _from_layer1(intent, text):
        normalized = _asr_normalize(text)
        sq = _extract_full_query(normalized)
        _L1_NOISE = frozenset({"now","okay","please","ple","ase","like","related","find","open","show","about","how","the","get"})
        sq = " ".join(t for t in sq.split() if t.lower() not in _L1_NOISE and len(t) > 2)
        kw = sq.split()
        return {"reconstructed": normalized, "intent": intent, "search_query": sq,
                "document_name": "", "instansi": "", "jenis": "", "tahun": "",
                "keywords_en": kw, "keywords_id": kw, "keywords": kw,
                "reasoning": "Layer-1 (Flash fallback)", "_is_hint": False}

    @staticmethod
    def _default(raw="", error=""):
        return {"reconstructed": raw, "intent": "chitchat", "search_query": "",
                "document_name": "", "instansi": "", "jenis": "", "tahun": "",
                "keywords_en": [], "keywords_id": [], "keywords": [],
                "reasoning": f"fallback{' ERR:'+error if error else ''}", "_is_hint": False}


_EXPLICIT_OPEN_RE = re.compile(
    r"\b(tampilkan\s+(panel|repositori|daftar|berita|peraturan|hasil)|"
    r"show\s+(me\s+)?the\s+repositor(y|i)?|show\s+(me\s+)?the\s+(list|panel|news|results?)|"
    r"open\s+(the\s+)?repositor(y|i)?|open\s+(the\s+)?panel|"
    r"buka\s+(repositori|panel|daftar)|lihat\s+(repositori|daftar|panel)|"
    r"show\s+(me\s+)?the\s+news|tampilkan\s+berita|buka\s+berita|"
    r"open\s+repositor(y|i)?|tampilkan\s+hasil|show\s+results?|"
    r"pull\s+up\s+(the\s+)?repositor|"
    r"can\s+you\s+open\s+(the\s+)?repositor|please\s+open\s+(the\s+)?repositor|"
    r"show\s+me\s+(a\s+|the\s+)?document\s+.*\s+(on|in)\s+(the\s+)?repositor|"
    r"(on|in)\s+(the\s+)?repositor(y|i)|"
    r"di\s+repositori|dalam\s+repositori|tampilkan\s+di\s+repositori)\b", re.I,
)


def is_explicit_open(text: str) -> bool:
    return bool(_EXPLICIT_OPEN_RE.search(text)) or bool(_EXPLICIT_OPEN_RE.search(_asr_normalize(text)))