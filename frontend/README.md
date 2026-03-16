# Legalitik Live Agent 🎙️⚖️

A real-time, voice-activated AI agent interface built for exploring, analyzing, and interacting with legal documents and regulations. This project features a dynamic 3D audio visualizer and seamless WebSocket integration for two-way voice communication with a Gemini-powered backend.

## ✨ Key Features

* **Real-Time Voice Interaction:** Streams raw microphone audio via AudioWorklet and WebSockets, and plays back base64-encoded AI voice responses in real-time with ultra-low latency.
* **Audio-Reactive 3D Visualizer:** Uses Three.js and custom GLSL shaders (Simplex Noise) to render a dynamic, glowing orb that reacts smoothly to microphone input (volume, bass, mid, and treble).
* **Intelligent UI Panels:** A slide-out control panel that dynamically updates based on the AI Agent's tool calls:
    * 📚 **Repository:** Browse the latest legal regulations and search results.
    * 🔍 **Deep Search Preview:** Break down document structures (preamble, chapters, sections, verses) with automated keyword highlighting and auto-scrolling.
    * 📊 **Regulation Details:** View summaries, statistics, and legal relationships (e.g., which laws it revokes or implements).
    * 📰 **Latest News:** Fetch real-time legal and financial news based on context.
* **Smooth Animations:** Powered by Framer Motion for seamless transitions between panel views and repository carousels.

## 🛠️ Tech Stack

* **Frontend Framework:** React (with TypeScript) + Vite
* **3D Graphics:** Three.js (WebGL, Custom Vertex & Fragment Shaders)
* **Animations & Icons:** Framer Motion, Lucide React
* **Styling:** Tailwind CSS
* **Audio Processing:** Web Audio API (AudioContext, AnalyserNode, AudioWorklet)
* **Network & Tunneling:** WebSockets (Real-time bi-directional streaming), ngrok (Localhost exposing)

## 🚀 Getting Started

### Prerequisites
Make sure you have [Node.js](https://nodejs.org/) installed on your machine.

### Quick Start (Installation & Run)

Copy and paste the following block into your terminal to install dependencies, setup the local environment variable, and start the development server all at once:

```bash
npm install && echo "VITE_WS_URL=ws://localhost:8080/ws" > .env && npm run dev