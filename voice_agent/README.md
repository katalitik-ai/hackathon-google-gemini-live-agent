# ⚖️ Legalitik: AI Voice-Powered Legal Intelligence

**Legalitik** is a high-performance, real-time voice assistant designed to navigate the complex landscape of Indonesian legal regulations (BI, OJK, LPS, UU, PP). Powered by **Google Vertex AI (Gemini Live)** and **Elasticsearch**, Legalitik allows users to "talk to the law" with sub-second latency and high factual accuracy.

---

## 🚀 Key Features

* **🎙️ Real-Time Voice RAG:** Query legal databases naturally using voice. The system retrieves relevant articles and provides concise, cited answers.
* **🌐 Seamless Language Mirroring:** Automatically detects and mirrors the user's language (Indonesian or English) on every turn.
* **🧠 Intelligent Contextual Memory:** Remembers user preferences, facts, and recently discussed documents to handle complex follow-up questions.
* **⚡ Six-Path Intent Routing:** Advanced classification logic that differentiates between conversational chitchat, deep legal research, and UI navigation.
* **📰 Live Legal News:** Integrates real-time RSS feeds to provide the latest updates on Indonesian financial and legal sectors.

---

## 🛠️ Tech Stack

* **Orchestration:** [FastAPI](https://fastapi.tiangolo.com/) (Python)
* **AI Engine:** [Google Vertex AI](https://cloud.google.com/vertex-ai) (Gemini Live & Gemini Flash)
* **Vector/Search DB:** [Elasticsearch](https://www.elastic.co/)
* **Package Management:** [uv](https://github.com/astral-sh/uv)
* **Frontend:** Vanilla JS / HTML5 (optimized for real-time audio visualization)

---

## 📦 Project Structure

```text
voice_agent/
├── .venv/                 # Managed by uv
├── app/                   # Core application logic
│   ├── classifier.py      # Intent classification (Flash)
│   ├── config.py          # Application settings
│   ├── frontend.html      # Browser UI
│   ├── function_calling.py# Tool execution logic
│   ├── main.py            # FastAPI entry point & WebSocket
│   ├── memory.py          # STM & Long-term memory logic
│   ├── news_tools.py      # RSS news fetching
│   ├── prompts.py         # AI System Instructions
│   ├── rag_tools.py       # Elasticsearch integration
│   └── vertex.py          # Vertex AI client factory
├── memory/
│   └── agent_memory.json  # Local long-term memory storage
├── pyproject.toml         # uv project configuration
├── README.md              # Project documentation
├── requirements.txt       # Legacy dependency list
├── run.py                 # Startup script
└── uv.lock                # uv lockfile for reproducible builds

```

---

## ⚙️ Quick Start

### 1. Installation (Using `uv`)

Sync your environment and install all dependencies instantly:

```bash
uv sync

```

### 2. Environment Setup

Create a `.env` file in the root directory:

```env
# --- Elasticsearch Configuration ---
ES_HOST=http://localhost:9200/
ES_USER=user
ES_PASS=pass
ES_INDEX=index-1
ES_INDEX_SEARCH=index-2

# --- Vertex AI Configuration ---
VERTEX_PROJECT=project-id
VERTEX_LOCATION=location
VERTEX_KEY_PATH=key.json

# --- Gemini Model Settings ---
LIVE_MODEL=gemini-live-2.5-flash-native-audio
FLASH_MODEL=gemini-2.5-flash-lite
GEMINI_VOICE=Puck

# --- Server Settings ---
PORT=8001
USE_NGROK=True
NGROK_AUTH_TOKEN=token-ngrok

```

### 3. Execution

Launch the agent:

```bash
uv run run.py

```

---

## 🧠 Intelligence & Routing Logic

| Path | Interaction | UI Behavior | Latency |
| --- | --- | --- | --- |
| **Search** | Detailed legal queries (e.g., "What is GWM?") | **Voice-Only** (Focus) | ~0.5s |
| **Docs** | "Open Peraturan Bank Indonesia nomor 17 tahun 2015" | **Opens Document Panel** | ~0.6s |
| **News** | "Latest OJK updates" | **Voice + Optional Panel** | ~1.2s |
| **Nav** | "Close panel", "Go back" | **Dynamic UI Updates** | 0ms |

---

## 🛡️ Safety & Reliability

* **Anti-Hallucination:** The agent is strictly grounded in the legal database. If a regulation is missing, it will explicitly state it cannot find the info rather than inventing content.
* **Zero-Latency Feedback:** Uses a Layer-0 classification path to handle chitchat and navigation instantly without waiting for LLM processing.
* **Silence-Optimized:** The agent remains silent while searching the database to avoid confusing "filler" audio.

---

*Developed for the Gemini Live Agent Challenge (Google Cloud)- Hackathon 2026.*