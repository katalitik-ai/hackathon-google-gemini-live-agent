# ⚖️ Legalitik: AI Voice-Powered Legal Intelligence

**Legalitik** is a high-performance, real-time voice assistant designed to navigate the complex landscape of Indonesian legal regulations (BI, OJK, LPS, UU, PP). Built for the **Gemini Live Agent Challenge (Google Cloud) - Hackathon 2026**, Legalitik allows users to literally "talk to the law" with sub-second latency, high factual accuracy, and a stunning 3D audio-reactive interface.

This repository houses the complete Legalitik ecosystem, consisting of four core microservices: an **Automated ETL Pipeline**, a **Real-Time Voice Agent**, a **Secure Auth Backend**, and a **Dynamic 3D Frontend**.

---

## ✨ Key Features

* 🎙️ **Real-Time Voice RAG:** Query legal databases naturally using voice. Powered by **Google Vertex AI (Gemini Live)** and **Elasticsearch**, it retrieves relevant articles and provides concise, cited answers with ultra-low latency.
* 🌌 **Audio-Reactive 3D Visualizer:** A stunning React frontend featuring a dynamic Three.js glowing orb that reacts smoothly to raw microphone input (volume, bass, mid, and treble) via custom GLSL shaders.
* 🧠 **Intelligent Contextual Memory & Routing:** Features a 6-path intent routing system (Layer-0 classification) to instantly differentiate between chitchat, deep legal research, and dynamic UI navigation.
* 📜 **Automated Legal Ingestion:** A robust Python ETL pipeline that scrapes the Bank Indonesia website, performs OCR on PDFs (using Tesseract/PyMuPDF), and indexes document metadata and full text into Elasticsearch.
* 🔐 **Secure User Management:** A dedicated Node.js backend handling user registration, seamless login, bcrypt password hashing, and active session tracking.
* 🌐 **Seamless Language Mirroring & News:** Automatically detects and mirrors the user's language (Indonesian or English) and integrates real-time RSS feeds for the latest financial/legal updates.

---

## 🛠️ Mega Tech Stack

| Domain | Technologies Used |
| --- | --- |
| **Frontend & UI** | React, TypeScript, Vite, Three.js (WebGL/GLSL), Framer Motion, Tailwind CSS, Web Audio API |
| **Voice & AI Engine** | FastAPI (Python), Google Vertex AI (Gemini Live & Flash), `uv` package manager |
| **Data & Search** | Elasticsearch, Vector Search |
| **ETL Pipeline** | Python, `pdfplumber`, PyMuPDF, Tesseract OCR, BeautifulSoup |
| **Auth Backend** | Node.js, Express, Bcrypt |
| **Networking** | WebSockets (Bi-directional streaming), ngrok |

---

## 📂 Project Structure

At the root level, the project is organized into four distinct, decoupled services:

```text
legalitik-root/
├── ETL/                 # Python scripts & notebooks for scraping, OCR, and ES indexing
├── backend/             # Node.js/Express API for user authentication and session management
├── frontend/            # React/Vite web application with 3D audio visualization
├── voice_agent/         # FastAPI Python server handling WebSockets and Gemini AI logic
└── .gitignore           # Root git ignore file

```

---

## 🧩 System Architecture

Here is how the four directories work together:

1. **The Voice Agent (`/voice_agent`):** The brain of the operation. Orchestrated via FastAPI, it handles the WebSocket connection from the frontend, manages conversational memory, streams audio to/from Gemini Live, and executes tool calls (RAG searches in Elasticsearch, fetching RSS news, or triggering UI panel updates).
2. **The Frontend (`/frontend`):** The user's portal. It captures raw mic audio via AudioWorklet, streams it to the Voice Agent, and instantly plays back base64-encoded AI audio. It features slide-out UI panels that dynamically update based on what the AI is talking about.
3. **The Auth API (`/backend`):** A lightweight Express server managing user states, ensuring only authenticated users can access the Live Agent, and tracking active sessions.
4. **The ETL Pipeline (`/ETL`):** The data feeder. A set of Python notebooks and scripts (`scrape → download → OCR → parse → ingest`) that populate the Elasticsearch `legalitik-searching` and `legalitik-knowledge-based` indices with fresh regulation data.

---

## 🚀 Quick Start & Setup Guide

To run the full Legalitik ecosystem locally, open four separate terminal windows and follow these steps sequentially:

### Step 1: Elasticsearch Setup

Ensure **Elasticsearch** is installed and running locally on your machine.

* Default host: `http://localhost:9200`

### Step 2: Auth Backend (Node.js)

Navigate to the backend directory, install dependencies, and start the server:

```bash
cd backend
npm install

```

*Make sure to create your `.env` file here containing your database credentials and `PORT=3000`.*

```bash
node index.js

```

### Step 3: Voice Agent (FastAPI + AI)

Navigate to the voice agent directory and sync the Python environment using `uv`:

```bash
cd voice_agent
uv sync

```

*Create a `.env` file with your `ES_HOST`, `VERTEX_PROJECT`, `VERTEX_KEY_PATH`, and Gemini configurations as detailed in the Voice Agent's internal readme.*

```bash
uv run run.py

```

### Step 4: Frontend UI (React)

Navigate to the frontend directory, install dependencies, set the WebSocket URL, and start the dev server:

```bash
cd frontend
npm install
echo "VITE_WS_URL=<URL_FROM_STEP_3>" > .env
npm run dev

```

### Optional: Running the ETL Pipeline

If you are running this on a fresh machine and need to populate your empty Elasticsearch instance:

```bash
cd ETL
pip install -r requirements.txt
sudo apt install tesseract-ocr tesseract-ocr-ind unoconv

```

*Configure your `.env` with Elasticsearch credentials, then open and run the `crawl_ingest.ipynb` notebook.*

---

## 🛡️ Hackathon Notes on Safety & Reliability

* **Anti-Hallucination:** Grounded strictly in the `legalitik-knowledge-based` index. If a regulation is missing, Gemini will explicitly state it cannot find the info rather than inventing content.
* **Silence-Optimized:** The agent remains silent while searching the database to avoid confusing filler audio, ensuring a crisp, professional legal consultation experience.

---
