import os
import re
import pdfplumber
import fitz  # PyMuPDF
from elasticsearch import Elasticsearch, helpers
from datetime import datetime
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

ES_HOST    = os.getenv("ES_HOST", "http://localhost:9200")
ES_USER    = os.getenv("ES_USER", "elastic")
ES_PASS    = os.getenv("ES_PASS")
ES_INDEX_1 = os.getenv("ES_INDEX_1", "temp-searching")
ES_INDEX_2 = os.getenv("ES_INDEX_2", "temp-knowledge-based")
PDF_FOLDER = os.getenv("PDF_FOLDER", "downloaded_pdfs")

# Regex patterns dari parser_fixing_mengingat

# Nama jenis peraturan (ambil dari 100 kata pertama)
NAMA_PATTERN = re.compile(
    r'(ketetapan|surat|peraturan|keputusan|instruksi|undang)(.*?)(republik indonesia|nomor|\sno\s)',
    re.IGNORECASE
)

# Nomor + tahun dari satu klausa yang sama: "nomor X tahun YYYY"
# Mencegah tahun dari klausa lain (misal: "nomor 23 tahun 2025") ikut ke-match
NOMOR_TAHUN_PATTERN = re.compile(
    r'nomor\s+(\S+)\s+tahun\s+((19|20)\d{2})',
    re.IGNORECASE
)

# Tentang: ambil teks antara "tentang" dan kata pembatas berikutnya
RI_NEG = r'(?<!berdirinya\s)(?<!di\s)(?<!negara\s)(?<!dan\s)(?<!dari\s)'
TENTANG_STOP = '|'.join([
    RI_NEG + r'republik indonesia\s(?!serikat)',
    r'dengan rahmat .+? esa',
    r'menimbang',
    r'(?<!peraturan yang menetapkan\s)bahwa',
    r'kami, presiden',
])
TENTANG_PATTERN = re.compile(
    r'tentang(.*?)(?:{})'.format(TENTANG_STOP),
    re.IGNORECASE | re.DOTALL
)


# ── ES Client ─────────────────────────────────────────────────────────────────

def get_es_client():
    es = Elasticsearch(
        hosts=[ES_HOST],
        basic_auth=(ES_USER, ES_PASS),
        verify_certs=False,
        request_timeout=60
    )
    assert es.ping(), "Elasticsearch tidak bisa dijangkau!"
    return es


def ensure_index(es, index, mappings):
    if es.indices.exists(index=index):
        return
    es.indices.create(index=index, body={
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {"properties": mappings}
    })
    print(f"Index '{index}' berhasil dibuat")


BASE_MAPPINGS = {
    "nama_file": {"type": "keyword"},
    "jenis":     {"type": "keyword"},
    "nomor":     {"type": "keyword"},
    "tahun":     {"type": "integer"},
    "judul":     {"type": "text"},
    "tentang":   {"type": "text"},
    "timestamp": {"type": "date"}
}

DETAIL_MAPPINGS = {
    **BASE_MAPPINGS,
    "full_text": {"type": "text"}
}


# ── OCR / Text Extraction ─────────────────────────────────────────────────────

def ocr_with_pymupdf(filepath, max_pages=None):
    """OCR pakai PyMuPDF. Dipakai kalau pdfplumber tidak dapat teks (PDF scan)."""
    text = ""
    try:
        doc = fitz.open(filepath)
        pages = list(doc) if max_pages is None else list(doc)[:max_pages]
        for page in pages:
            page_text = page.get_text()
            if page_text.strip():
                text += page_text + "\n"
            else:
                # Fallback ke OCR per halaman
                mat = fitz.Matrix(2, 2)  # scale 2x untuk kualitas OCR lebih baik
                page.get_pixmap(matrix=mat)
                page_text = page.get_textpage_ocr().extractText()
                text += page_text + "\n"
        doc.close()
    except Exception as e:
        print(f"  PyMuPDF error: {e}")
    return text


def extract_text_from_pdf(filepath, max_pages=5, full=False):
    """
    Extract teks dari PDF.
    1. Coba pdfplumber (cepat, untuk PDF digital)
    2. Fallback ke PyMuPDF OCR kalau teks tidak ditemukan (PDF scan)
    """
    limit = None if full else max_pages
    text = ""

    try:
        with pdfplumber.open(filepath) as pdf:
            pages = pdf.pages if full else pdf.pages[:max_pages]
            for page in pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print(f"  pdfplumber error: {e}")

    if not text.strip():
        print(f"  Teks kosong, fallback ke PyMuPDF OCR...")
        text = ocr_with_pymupdf(filepath, max_pages=limit)

    return text


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_regulation(filepath):
    """Parse metadata peraturan dari konten PDF, pakai pola dari parser_fixing_mengingat."""
    filename = os.path.basename(filepath)
    text = extract_text_from_pdf(filepath)

    if not text.strip():
        print(f"  Tidak ada teks ditemukan di {filename}")
        return None

    # Normalize teks: lowercase, hapus karakter non-ASCII
    text_clean = re.sub(r'[^0-9a-z!"#$%&\'()*+,-./:;<=>\[\] ]', '', text.lower())
    text_clean = ' '.join(text_clean.split())

    # Ambil 100 kata pertama untuk ekstrak nama jenis peraturan
    text50 = ' '.join(text_clean.split()[:100])

    # ── Nama (jenis peraturan) ────────────────────────────────────────────────
    nama_matches = re.findall(
        r'(ketetapan|surat|peraturan|keputusan|instruksi|undang)(.*?)(republik indonesia|nomor|\sno\s)',
        text50
    )
    if nama_matches:
        nama = (nama_matches[0][0] + nama_matches[0][1]).strip()
        nama = re.sub(r'\bno\b', '', nama).replace('nomor', '').replace('republik indonesia', '').strip()
        # fix undang → undang-undang
        if nama == 'undang':
            nama = 'undang-undang'
    else:
        nama = 'None'

    # ── Nomor + Tahun (dari klausa yang sama) ────────────────────────────────
    # Cari di 200 kata pertama dulu (judul), fallback ke full text
    text_head = ' '.join(text_clean.split()[:200])
    nomor_tahun_match = NOMOR_TAHUN_PATTERN.search(text_head) or NOMOR_TAHUN_PATTERN.search(text_clean)

    nomor = None
    tahun = None
    if nomor_tahun_match:
        nomor = re.sub(r'\s*/\s*', '/', nomor_tahun_match.group(1).strip())
        tahun = int(nomor_tahun_match.group(2))

    # ── Tentang ───────────────────────────────────────────────────────────────
    tentang_match = TENTANG_PATTERN.search(text_clean)
    tentang = None
    if tentang_match:
        tentang = re.sub(r'\s+', ' ', tentang_match.group(1)).strip()
        # Potong di tanda baca kalimat pertama
        tentang = re.split(r';\s*(?!tambahan)', tentang)[0]
        tentang = tentang.split(' perlu ')[0].split(' bismil')[0].strip()[:500]

    # ── Judul (nama + nomor + tahun + tentang) ────────────────────────────────
    parts = [p for p in [nama if nama != 'None' else None,
                         f"nomor {nomor}" if nomor else None,
                         f"tahun {tahun}" if tahun else None,
                         f"tentang {tentang}" if tentang else None] if p]
    judul = ' '.join(parts) if parts else os.path.splitext(filename)[0].replace('_', ' ')

    return {
        "nama_file": filename,
        "jenis":     nama,
        "nomor":     nomor,
        "tahun":     tahun,
        "judul":     judul,
        "tentang":   tentang,
        "timestamp": datetime.utcnow().isoformat()
    }


# ── Ingest ────────────────────────────────────────────────────────────────────

def ingest_pdfs(pdf_folder=PDF_FOLDER, batch_size=100):
    pdf_files = [f for f in os.listdir(pdf_folder) if f.endswith(".pdf")]
    if not pdf_files:
        print(f"Tidak ada PDF di folder '{pdf_folder}'")
        return

    print(f"Ditemukan {len(pdf_files)} PDF, mulai proses...\n")

    es = get_es_client()
    ensure_index(es, ES_INDEX_1, BASE_MAPPINGS)
    ensure_index(es, ES_INDEX_2, DETAIL_MAPPINGS)

    existing = set()
    try:
        res = es.search(index=ES_INDEX_1, body={
            "_source": ["nama_file"],
            "query": {"match_all": {}},
            "size": 10000
        })
        existing = {hit["_source"]["nama_file"] for hit in res["hits"]["hits"]}
        print(f"Sudah ter-ingest sebelumnya: {len(existing)} file\n")
    except Exception:
        pass

    batch_1 = []
    batch_2 = []
    skipped = success = failed = 0

    for filename in tqdm(pdf_files, desc="Parsing PDF"):
        if filename in existing:
            skipped += 1
            continue

        filepath = os.path.join(pdf_folder, filename)
        doc = parse_regulation(filepath)

        if not doc:
            failed += 1
            continue

        batch_1.append({"_index": ES_INDEX_1, "_id": filename, "_source": doc})

        full_text = extract_text_from_pdf(filepath, full=True)
        batch_2.append({"_index": ES_INDEX_2, "_id": filename, "_source": {**doc, "full_text": full_text}})

        if len(batch_1) >= batch_size:
            helpers.bulk(es, batch_1, raise_on_error=False)
            helpers.bulk(es, batch_2, raise_on_error=False)
            success += len(batch_1)
            batch_1.clear()
            batch_2.clear()

    if batch_1:
        helpers.bulk(es, batch_1, raise_on_error=False)
        helpers.bulk(es, batch_2, raise_on_error=False)
        success += len(batch_1)

    print(f"\nSelesai!")
    print(f"  Berhasil : {success} (→ {ES_INDEX_1} & {ES_INDEX_2})")
    print(f"  Dilewati : {skipped} (sudah ada)")
    print(f"  Gagal    : {failed}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ingest_pdfs()