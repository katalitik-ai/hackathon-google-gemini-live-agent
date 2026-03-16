# 📜 BI Regulation Scraper & Ingest Pipeline

A pipeline for **scraping, downloading, OCR processing, and ingesting**
regulations from the Bank Indonesia website:

https://www.bi.go.id/id/publikasi/peraturan

The extracted data is stored in **Elasticsearch** to enable search and
analysis.

------------------------------------------------------------------------

# ⚙️ Setup

Install Python dependencies:

``` bash
pip install -r requirements.txt
```

Install system dependencies (OCR + document conversion):

``` bash
sudo apt install tesseract-ocr tesseract-ocr-ind unoconv
```

------------------------------------------------------------------------

# 🔑 Environment Variables

Create a `.env` file:

``` env
ES_HOST=http://localhost:9200
ES_USER=elastic
ES_PASS=your_password

ES_INDEX_1=legalitik-searching
ES_INDEX_2=legalitik-knowledge-based

PDF_FOLDER=downloaded_pdfs
```

------------------------------------------------------------------------

# 🚀 Usage

The scraping and ingestion pipeline is demonstrated in the notebook:

👉 **`crawl_ingest.ipynb`**

Open the notebook and run the cells sequentially to execute the full pipeline:
- Crawl regulation pages
- Download PDFs
- Perform OCR if needed
- Ingest data into Elasticsearch

------------------------------------------------------------------------

# 🧩 How It Works

The pipeline consists of three main components:

### 🕷️ `scraper.py`

-   Crawls the Bank Indonesia regulation publication page
-   Extracts basic metadata
-   Downloads regulation PDF files

### 📄 `bi_ingest.py`

-   Extracts text from PDFs using:
    -   **pdfplumber**
    -   fallback **PyMuPDF OCR**
-   Parses metadata from documents
-   Ingests data into Elasticsearch

### 🔁 `run_pipeline.py`

Runs the entire process end-to-end:

    scrape → download → OCR → ingest

------------------------------------------------------------------------

# 🗄️ Elasticsearch Index Structure

  Index                     Description
  ------------------------- -------------------------------
  `legalitik-searching`          Regulation metadata
  `legalitik-knowledge-based`   Metadata + full document text

------------------------------------------------------------------------

# 📦 Sample Document

``` json
{
  "file_name": "PADG-012026.pdf",
  "type": "peraturan anggota dewan gubernur",
  "number": "1",
  "year": 2026,
  "about": "perubahan atas peraturan anggota dewan gubernur nomor 23 tahun 2025 ...",
  "title": "peraturan anggota dewan gubernur nomor 1 tahun 2026",
  "timestamp": "2026-03-16T13:51:08.321027"
}
```

------------------------------------------------------------------------

# 📊 Pipeline Output Example

    Found 150 PDFs, starting process...

    Parsing PDF: 100%|████████| 150/150
      Text empty, falling back to PyMuPDF OCR...

    Done!
      Success : 148 (→ legalitik-searching & legalitik-knowledge-based)
      Skipped : 0 (already ingested)
      Failed  : 2

------------------------------------------------------------------------

# 🧠 Notes

-   OCR is only used if the PDF text cannot be extracted directly.
-   The pipeline supports **incremental ingestion**, so documents that
    already exist in Elasticsearch will be skipped.
