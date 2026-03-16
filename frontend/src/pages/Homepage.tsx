import { useState, useEffect, useRef, useMemo } from 'react';
import type { ReactNode } from 'react';
import * as THREE from 'three';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Mic, MicOff, ChevronLeft, ChevronRight,
  Database, ChevronUp, ChevronDown,
  Info, FileSearch, Newspaper, Search
} from 'lucide-react';

// ==========================================
// 1. THREE.JS SHADERS
// ==========================================
const noiseShader = `
vec4 permute(vec4 x){return mod(((x*34.0)+1.0)*x, 289.0);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159 - 0.85373472095314 * r;}
float snoise(vec3 v){ 
  const vec2 C = vec2(1.0/6.0, 1.0/3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i  = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod(i, 289.0); 
  vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 1.0/7.0;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z *ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ *ns.x + ns.yyyy;
  vec4 y = y_ *ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0)*2.0 + 1.0;
  vec4 s1 = floor(b1)*2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;
  vec3 p0 = vec3(a0.xy,h.x);
  vec3 p1 = vec3(a0.zw,h.y);
  vec3 p2 = vec3(a1.xy,h.z);
  vec3 p3 = vec3(a1.zw,h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
}
`;

const vertexShader = `
  ${noiseShader}
  uniform float uTime;
  uniform float uVolume;
  uniform float uBass;
  uniform float uMid;
  uniform float uTreble;
  varying vec2 vUv;
  varying float vDisplacement;
  varying vec3 vNormal;

  void main() {
    vUv = uv;
    vNormal = normal;
    float idleNoise = snoise(position * 1.5 + uTime * 0.2) * 0.15;
    float bassNoise = snoise(position * (1.0 + uBass * 0.5) - uTime * 0.5) * (uBass * 0.5);
    float midNoise = snoise(position * 3.0 + uTime) * (uMid * 0.15);
    float trebleNoise = snoise(position * 8.0 + uTime * 2.0) * (uTreble * 0.08);
    float displacement = idleNoise + ((bassNoise + midNoise + trebleNoise) * (1.0 + uVolume));
    vDisplacement = displacement;
    vec3 newPosition = position + normal * displacement;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(newPosition, 1.0);
  }
`;

const fragmentShader = `
  uniform float uTime;
  uniform float uVolume;
  varying vec2 vUv;
  varying float vDisplacement;
  varying vec3 vNormal;

  void main() {
    vec3 colorCyan = vec3(0.0, 0.9, 1.0);
    vec3 colorMagenta = vec3(1.0, 0.0, 0.8);
    vec3 colorPurple = vec3(0.5, 0.1, 1.0);
    vec3 colorBlue = vec3(0.1, 0.2, 1.0);

    float mix1 = sin(vUv.x * 10.0 + uTime) * 0.5 + 0.5;
    float mix2 = cos(vUv.y * 8.0 - uTime * 0.8) * 0.5 + 0.5;
    
    vec3 baseColor = mix(colorCyan, colorMagenta, mix1);
    baseColor = mix(baseColor, colorPurple, mix2);
    
    float displacementFactor = smoothstep(-0.1, 0.4, vDisplacement);
    baseColor = mix(baseColor, colorBlue, displacementFactor);

    float glow = max(0.0, vDisplacement * 2.5);
    vec3 finalColor = baseColor + vec3(1.0, 0.9, 1.0) * glow * (0.5 + uVolume);

    float fresnel = dot(vNormal, vec3(0.0, 0.0, 1.0));
    fresnel = clamp(1.0 - fresnel, 0.0, 1.0);
    fresnel = pow(fresnel, 3.0);
    finalColor += colorCyan * fresnel * 0.8;

    gl_FragColor = vec4(finalColor, 1.0);
  }
`;

// ==========================================
// 2. UI COMPONENTS & UTILS
// ==========================================
interface CardProps { title?: ReactNode; subtitle?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; }
function Card({ title, subtitle, action, children, className = "" }: CardProps) {
  return (
    <div className={"relative rounded-2xl bg-white/90 shadow-[0_1px_0_0_rgba(0,0,0,0.02),0_20px_40px_-20px_rgba(0,0,0,0.08)] backdrop-blur border border-slate-200/60 flex flex-col overflow-hidden " + className}>
      {(title || action || subtitle) && (
        <div className="flex-shrink-0 flex items-start justify-between gap-3 p-5 pb-3 bg-white/50 border-b border-slate-100/50">
          <div>
            {title && <h3 className="text-sm font-semibold tracking-tight text-slate-800">{title}</h3>}
            {subtitle && <p className="mt-1 text-[12px] text-slate-500 leading-relaxed">{subtitle}</p>}
          </div>
          {action && <div>{action}</div>}
        </div>
      )}
      <div className="p-5 flex-1 min-h-0 overflow-hidden flex flex-col">
        {children}
      </div>
    </div>
  );
}

function GhostButton({ icon: Icon, children, className = "", onClick }: { icon?: any, children?: ReactNode, className?: string, onClick?: () => void }) {
  return (
    <button onClick={onClick} className={"inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200/70 bg-white/80 px-3 py-2 text-sm font-medium text-slate-700 transition-all hover:-translate-y-[1px] hover:shadow-sm hover:border-red-400/70 focus:border-red-400 " + className}>
      {Icon && <Icon className="h-4 w-4" />}
      {children}
    </button>
  );
}

function IconButton({ icon: Icon, onClick, title, className = "" }: { icon: any, onClick?: () => void, title?: string, className?: string }) {
  return (
    <button onClick={onClick} title={title} className={"inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200/70 bg-white/80 text-slate-600 transition hover:-translate-y-[1px] hover:shadow-sm hover:border-red-400/70 " + className}>
      <Icon className="h-4 w-4" />
    </button>
  );
}

const renderHighlight = (text: string, keywords: string[]) => {
  if (!text) return "";
  if (!keywords || keywords.length === 0) return text;
  
  const safeKeywords = keywords.filter(Boolean).map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (safeKeywords.length === 0) return text;

  const regex = new RegExp(`(${safeKeywords.join('|')})`, 'gi');
  const parts = text.split(regex);
  
  return parts.map((part, i) =>
    regex.test(part) ? (
      <mark key={i} className="bg-yellow-200/80 text-yellow-900 rounded px-1 animate-pulse shadow-sm">
        {part}
      </mark>
    ) : part
  );
};

type PanelViewType = 'repository' | 'preview' | 'detail' | 'news';

// ==========================================
// 3. MAIN APPLICATION
// ==========================================
export default function Homepage() {
  const mountRef = useRef<HTMLDivElement>(null);
  const previewScrollRef = useRef<HTMLDivElement>(null);

  // UI & Data State
  const [isMicOn, setIsMicOn] = useState<boolean>(false);
  const [isPanelOpen, setIsPanelOpen] = useState<boolean>(false);
  const [panelView, setPanelView] = useState<PanelViewType>('repository');
  const [selectedDocTitle, setSelectedDocTitle] = useState<string>("");
  const [documents, setDocuments] = useState<any[]>([]);
  const [newestDocs, setNewestDocs] = useState<any[]>([]);
  const [newsArticles, setNewsArticles] = useState<any[]>([]); 
  const [searchQuery, setSearchQuery] = useState<string>(""); 
  const [isLoading, setIsLoading] = useState<boolean>(false);

  // Payload Data States
  const [docPreviewData, setDocPreviewData] = useState<any>(null);
  const [docDetailData, setDocDetailData] = useState<any>(null);
  const [activeKeywords, setActiveKeywords] = useState<string[]>([]);

  // Carousel State
  const [carouselPage, setCarouselPage] = useState(0);
  const [isCrawledExpanded, setIsCrawledExpanded] = useState(true);
  const carouselPages = useMemo(() => {
    const pages = [];
    for (let i = 0; i < newestDocs.length; i += 2) {
      pages.push(newestDocs.slice(i, i + 2));
    }
    return pages;
  }, [newestDocs]);

  // --- API & Audio Refs ---
  const wsRef = useRef<WebSocket | null>(null);

  // Recording
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const dataArrayRef = useRef<Uint8Array | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // Playback (Agent Voice)
  const playerCtxRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const activeSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  // WebGL material
  const materialRef = useRef<THREE.ShaderMaterial | null>(null);

  // Initial Log
  useEffect(() => {
    console.log('%c[UI] Ready — v23 Function Integration', 'color: #059669; font-weight: bold;');
  }, []);

  // Auto-scroll highlight
  useEffect(() => {
    if (activeKeywords.length > 0 && panelView === 'preview' && previewScrollRef.current) {
      setTimeout(() => {
        const mark = previewScrollRef.current?.querySelector('mark');
        if (mark) {
          mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 150);
    }
  }, [activeKeywords, panelView, docPreviewData]);

  // ========================================================
  // THREE JS SETUP (Visualizer)
  // ========================================================
  useEffect(() => {
    if (!mountRef.current) return;
    mountRef.current.innerHTML = '';

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 100);
    camera.position.z = 4;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mountRef.current.appendChild(renderer.domElement);

    const geometry = new THREE.SphereGeometry(1, 128, 128);
    const material = new THREE.ShaderMaterial({
      vertexShader, fragmentShader,
      uniforms: {
        uTime: { value: 0 }, uVolume: { value: 0 },
        uBass: { value: 0 }, uMid: { value: 0 }, uTreble: { value: 0 }
      },
      wireframe: false,
    });

    materialRef.current = material;
    const orb = new THREE.Mesh(geometry, material);
    scene.add(orb);

    const handleResize = () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    };
    window.addEventListener('resize', handleResize);

    const clock = new THREE.Clock();
    let animationFrameId: number;

    const animate = () => {
      const elapsedTime = clock.getElapsedTime();

      if (materialRef.current) {
        materialRef.current.uniforms.uTime.value = elapsedTime;

        if (analyserRef.current && dataArrayRef.current && isMicOn) {
          analyserRef.current.getByteFrequencyData(dataArrayRef.current as any);
          const data = dataArrayRef.current;
          let sumVolume = 0, sumBass = 0, sumMid = 0, sumTreble = 0;

          for (let i = 0; i < data.length; i++) {
            const val = data[i] / 255.0;
            sumVolume += val;
            if (i < 6) sumBass += val;
            else if (i < 46) sumMid += val;
            else sumTreble += val;
          }

          const lerp = (start: number, end: number, amt: number) => (1 - amt) * start + amt * end;
          const uniforms = materialRef.current.uniforms;

          uniforms.uVolume.value = lerp(uniforms.uVolume.value, (sumVolume / data.length) * 2.0, 0.1);
          uniforms.uBass.value = lerp(uniforms.uBass.value, sumBass / 6, 0.15);
          uniforms.uMid.value = lerp(uniforms.uMid.value, sumMid / 40, 0.1);
          uniforms.uTreble.value = lerp(uniforms.uTreble.value, sumTreble / 466, 0.1);
        } else {
          const uniforms = materialRef.current.uniforms;
          uniforms.uVolume.value *= 0.95;
          uniforms.uBass.value *= 0.95;
          uniforms.uMid.value *= 0.95;
          uniforms.uTreble.value *= 0.95;
        }
      }

      orb.rotation.y += 0.002;
      orb.rotation.x += 0.001;

      renderer.render(scene, camera);
      animationFrameId = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animationFrameId);
      if (mountRef.current && renderer.domElement && mountRef.current.contains(renderer.domElement)) {
        mountRef.current.removeChild(renderer.domElement);
      }
      geometry.dispose();
      material.dispose();
      renderer.dispose();
    };
  }, [isMicOn]);

  // ========================================================
  // WEBSOCKET & AUDIO STREAMING API
  // ========================================================

  const playAudioChunk = (base64String: string) => {
    if (!playerCtxRef.current || playerCtxRef.current.state === 'closed') {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      playerCtxRef.current = new AudioCtx({ sampleRate: 24000, latencyHint: 'interactive' });
      nextPlayTimeRef.current = playerCtxRef.current.currentTime;
    }
    const actx = playerCtxRef.current;
    if (actx.state === 'suspended') actx.resume();

    const bin = atob(base64String);
    const by = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) by[i] = bin.charCodeAt(i);
    const i16 = new Int16Array(by.buffer);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;

    const buf = actx.createBuffer(1, f32.length, 24000);
    buf.copyToChannel(f32, 0);

    const source = actx.createBufferSource();
    source.buffer = buf;
    source.connect(actx.destination);
    activeSourcesRef.current.push(source);

    source.onended = () => {
      const i = activeSourcesRef.current.indexOf(source);
      if (i > -1) activeSourcesRef.current.splice(i, 1);
    };

    const now = actx.currentTime;
    if (nextPlayTimeRef.current < now + 0.005) nextPlayTimeRef.current = now + 0.020;

    source.start(nextPlayTimeRef.current);
    nextPlayTimeRef.current += buf.duration;
  };

  const flushAudio = () => {
    activeSourcesRef.current.forEach(s => { try { s.stop(); s.disconnect(); } catch { } });
    activeSourcesRef.current = [];
    if (playerCtxRef.current) nextPlayTimeRef.current = playerCtxRef.current.currentTime;
  };

  const handleWebSocketMessage = (e: MessageEvent) => {
    try {
      const msg = JSON.parse(e.data);

      switch (msg.type) {
        case 'log':
          console.info(`[${msg.tag || 'SRV'}] ${msg.data}`);
          break;

        case 'session_ready':
          console.log('%c[SESSION] Gemini ready — speak now!', 'color: #059669; font-weight: bold;');
          break;

        case 'audioStream':
          playAudioChunk(msg.data);
          break;

        case 'transcript_user':
        case 'transcript_assistant':
          break;

        case 'turn_complete':
          console.log(`[SESSION] Turn complete · ${msg.chunks} chunks · ${msg.frags} frags`);
          setIsLoading(false); 
          break;

        case 'flash_result':
          console.log(`%c[FLASH] ${msg.data.intent} | sq:"${msg.data.search_query || ''}"`, 'color: #d97706; font-weight: bold;');
          
          const intent = msg.data.intent;
          
          // FAST-PATH
          if (intent === 'close_panel') {
            setIsPanelOpen(false);
          } else if (intent === 'back_to_repository') {
            setPanelView('repository');
            setIsPanelOpen(true);
          } else if (intent === 'highlight_keywords') {
            setActiveKeywords(msg.data.keywords || []);
            if (panelView !== 'preview') setPanelView('preview');
          }
          break;

        case 'tool_call':
          console.log(`%c[TOOL] Executing: ${msg.action}`, 'color: #0891b2; font-weight: bold;');
          setIsLoading(false);

          if (msg.action === 'open_repository') {
            setDocuments(msg.documents || msg.data?.documents || []);
            if (msg.newest && msg.newest.length > 0) setNewestDocs(msg.newest);
            setSearchQuery(msg.query || msg.data?.query || "");
            setPanelView('repository');
            setActiveKeywords(msg.keywords || msg.data?.keywords || []);
            
            setIsPanelOpen(true);

          } else if (msg.action === 'show_news') {
            setNewsArticles(msg.articles || msg.data?.articles || []);
            setSearchQuery(msg.query || msg.data?.query || "");
            setPanelView('news');
            
            setIsPanelOpen(true);

          } else if (msg.action === 'open_deep_search') {
            const data = msg.data || msg;
            setDocPreviewData(data);
            setSelectedDocTitle(data.kind2 || data.judul || "Deep Search");
            setActiveKeywords(data.keywords || msg.keywords || []);
            setPanelView('preview');
            setIsPanelOpen(true);

          } else if (msg.action === 'open_detail') {
            const data = msg.data || msg;
            setDocDetailData(data);
            setSelectedDocTitle(data.kind2 || data.judul || "Regulation Details");
            setPanelView('detail');
            setIsPanelOpen(true);

          } else if (msg.action === 'highlight_keywords') {
            setActiveKeywords(msg.keywords || []);
            if (panelView !== 'preview') setPanelView('preview');

          } else if (msg.action === 'close_panel') {
            setIsPanelOpen(false);

          } else if (msg.action === 'back_to_repository') {
            setPanelView('repository');
            setIsPanelOpen(true);
          }
          break;

        case 'quota_error':
          console.error('[QUOTA] Gemini API quota exhausted');
          setIsLoading(false);
          flushAudio();
          setIsMicOn(false);
          break;

        case 'flush_audio':
        case 'interrupted':
        case 'gemini_error':
          console.warn(`[SESSION] ${msg.type}`);
          setIsLoading(false);
          flushAudio();
          if (msg.type === 'gemini_error') setIsMicOn(false);
          break;

        default:
          break;
      }
    } catch (err) {
      console.error("[ERROR] Parsing WS message:", err);
    }
  };

  const toggleMic = async () => {
    if (isMicOn) {
      console.log('[SESSION] Ending session...');
      setIsMicOn(false);
      if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
      if (workletNodeRef.current) workletNodeRef.current.disconnect();
      if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop());
      if (audioCtxRef.current) { audioCtxRef.current.close(); audioCtxRef.current = null; }
      flushAudio();
    } else {
      try {
        console.log('[SESSION] Starting session...');
        const wsUrl = import.meta.env.VITE_WS_URL || 'ws://localhost:8080/ws';
        wsRef.current = new WebSocket(wsUrl);

        wsRef.current.onopen = () => console.log('%c[WS] Connected', 'color: #059669; font-weight: bold;');
        wsRef.current.onerror = () => console.error('[WS] Connection Error');
        wsRef.current.onclose = (e) => console.warn(`[WS] Closed. Code: ${e.code}`);

        wsRef.current.onmessage = handleWebSocketMessage;

        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, sampleRate: 16000 }
        });
        streamRef.current = stream;

        const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
        const audioCtx = new AudioCtx({ sampleRate: 16000 });
        audioCtxRef.current = audioCtx;

        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0.8;
        analyserRef.current = analyser;

        const source = audioCtx.createMediaStreamSource(stream);
        source.connect(analyser); 

        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        dataArrayRef.current = dataArray;

        const code = `class R extends AudioWorkletProcessor{buf=new Int16Array(256);idx=0;process(i){if(!i[0].length)return true;const f=i[0][0];for(let j=0;j<f.length;j++){this.buf[this.idx++]=Math.max(-32768,Math.min(32767,f[j]*32768|0));if(this.idx>=this.buf.length){this.port.postMessage(this.buf.slice(0,this.idx).buffer,[this.buf.slice(0,this.idx).buffer]);this.buf=new Int16Array(256);this.idx=0;}}return true;}}registerProcessor('gr',R);`;
        const blob = new Blob([code], { type: 'application/javascript' });
        await audioCtx.audioWorklet.addModule(URL.createObjectURL(blob));

        const workletNode = new AudioWorkletNode(audioCtx, 'gr');
        workletNodeRef.current = workletNode;

        let isStreamingReady = false;
        setTimeout(() => { isStreamingReady = true; }, 80);

        workletNode.port.onmessage = (e) => {
          if (!isStreamingReady || wsRef.current?.readyState !== 1) return;
          const b = new Uint8Array(e.data);
          let s = '', i = 0;
          for (; i < b.length; i += 1024) s += String.fromCharCode(...b.subarray(i, i + 1024));
          const base64Audio = btoa(s);
          wsRef.current.send(JSON.stringify({ type: 'realtimeInput', audioData: base64Audio }));
        };

        source.connect(workletNode); 
        setIsMicOn(true);
      } catch (err) {
        console.error("[ERROR] Failed to start mic/connection:", err);
      }
    }
  };

  return (
    <div className="w-full h-screen bg-[#FAFAFC] relative overflow-hidden font-sans text-slate-800">

      <div ref={mountRef} className={`absolute inset-0 z-0 flex items-center justify-center cursor-default transition-transform duration-500 ease-out ${isPanelOpen ? 'md:-translate-x-[425px]' : 'translate-x-0'}`} />

      <div className="absolute inset-0 z-10 pointer-events-none flex flex-col justify-between p-8 md:p-12">
        <header className="flex justify-between items-center opacity-80 mix-blend-multiply">
          <h1 className="text-sm tracking-widest uppercase font-semibold text-slate-400">Legalitik Live Agent</h1>
          {isMicOn && (
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse"></span>
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Live</span>
            </div>
          )}
        </header>

        <div className={`fixed bottom-8 md:bottom-12 left-1/2 flex flex-col items-center gap-3 pointer-events-auto w-full transition-transform duration-500 ease-out ${isPanelOpen ? 'md:-translate-x-[calc(50%+425px)]' : '-translate-x-1/2'}`}>
          <div className="flex items-center justify-center gap-4">
            <button onClick={toggleMic} className={`flex items-center justify-center w-14 h-14 md:w-16 md:h-16 rounded-full bg-white shadow-sm border transition-all cursor-pointer hover:scale-105 active:scale-95 ${isMicOn ? 'border-rose-200 text-rose-500 shadow-rose-500/20 ring-4 ring-rose-500/10' : 'border-slate-100 text-slate-600 hover:text-slate-900 hover:shadow-md'}`}>
              {isMicOn ? <Mic size={24} className="animate-pulse" /> : <MicOff size={24} />}
            </button>
          </div>
        </div>
      </div>

      <div className={`fixed top-4 bottom-4 right-4 w-[850px] max-w-[95vw] bg-slate-50/90 backdrop-blur-2xl shadow-2xl border border-slate-200/60 rounded-3xl flex flex-col transition-transform duration-500 ease-out z-50 overflow-hidden ${isPanelOpen ? 'translate-x-0' : 'translate-x-[110%]'}`}>
        
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200/60 bg-white/70">
          <div className="flex items-center gap-3">
            {panelView !== 'repository' && (
              <button onClick={() => setPanelView('repository')} className="p-1.5 border border-slate-200 rounded-lg text-slate-500 hover:bg-slate-50 hover:text-slate-800 transition-colors">
                <ChevronLeft size={16} />
              </button>
            )}
            <div className="flex bg-slate-100 rounded-full p-1">
              <div className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold tracking-wider bg-white text-slate-800 shadow-sm border border-slate-200/50">
                {panelView === 'repository' && <><Database size={14} className="text-slate-800" /> Repository</>}
                {panelView === 'preview' && <><FileSearch size={14} className="text-slate-800" /> Document Preview</>}
                {panelView === 'detail' && <><Info size={14} className="text-slate-800" /> Regulation Details</>}
                {panelView === 'news' && <><Newspaper size={14} className="text-slate-800" /> Latest News</>}
              </div>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6 scrollbar-hide" ref={previewScrollRef}>

          {/* VIEW: 1. REPOSITORY */}
          {panelView === 'repository' && (
            <div className="flex flex-col gap-6 animate-in fade-in duration-300">
              <div className="flex items-center gap-2 text-slate-600 mb-1">
                <Database size={18} />
                <span className="font-semibold text-sm">Legal Repository</span>
              </div>

              {newestDocs.length > 0 && (
                <Card title="Latest Regulations" subtitle={`Showing ${newestDocs.length} latest added regulations.`} action={<IconButton icon={isCrawledExpanded ? ChevronUp : ChevronDown} onClick={() => setIsCrawledExpanded(v => !v)} />}>
                  <AnimatePresence>
                    {isCrawledExpanded && (
                      <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                        <div className="relative">
                          <div className="overflow-hidden">
                            <motion.div className="flex" animate={{ x: `-${carouselPage * 100}%` }} transition={{ type: 'spring', stiffness: 200, damping: 25 }}>
                              {carouselPages.map((pageItems, pageIndex) => (
                                <div key={pageIndex} className="flex gap-4 w-full flex-shrink-0 items-stretch">
                                  {pageItems.map((doc: any) => (
                                    <div key={doc.id || doc.title} className="flex-1 w-[50%] flex flex-col">
                                      <div className="rounded-xl border border-slate-200 p-5 h-full flex flex-col justify-between hover:shadow-md hover:bg-white transition-all cursor-default group min-h-[160px]">
                                        <div className="space-y-3 flex-1">
                                          <h4 className="text-sm font-bold text-slate-800 leading-snug line-clamp-2 group-hover:text-red-600 transition-colors">{doc.title}</h4>
                                          <p className="text-xs text-slate-500 leading-relaxed line-clamp-2">{doc.about || doc.snippet}</p>
                                        </div>
                                        <div className="mt-4 pt-3 border-slate-200/80">
                                          <p className="text-[10px] font-bold text-red-600 uppercase tracking-wide">{doc.instansi || doc.source}</p>
                                        </div>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              ))}
                            </motion.div>
                          </div>
                          {carouselPage > 0 && (
                            <button onClick={() => setCarouselPage(p => p - 1)} className="absolute left-[-15px] top-1/2 -translate-y-1/2 z-10 h-8 w-8 rounded-full bg-white border border-slate-200 flex items-center justify-center shadow-md hover:bg-slate-50 hover:text-red-500 transition"><ChevronLeft className="h-4 w-4" /></button>
                          )}
                          {carouselPage < carouselPages.length - 1 && (
                            <button onClick={() => setCarouselPage(p => p + 1)} className="absolute right-[-15px] top-1/2 -translate-y-1/2 z-10 h-8 w-8 rounded-full bg-white border border-slate-200 flex items-center justify-center shadow-md hover:bg-slate-50 hover:text-red-500 transition"><ChevronRight className="h-4 w-4" /></button>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </Card>
              )}

              <div className="flex items-center justify-between mt-2 px-1">
                <span className="text-sm text-slate-600 font-medium">Showing {documents.length} documents</span>
              </div>

              <div className="flex flex-col gap-4">
                {isLoading && documents.length === 0 && (
                  <div className="text-center py-8 text-slate-400 text-sm animate-pulse">Loading documents from database...</div>
                )}
                {!isLoading && documents.length === 0 && (
                  <div className="text-center py-12 text-slate-400 text-sm border border-dashed border-slate-200 rounded-xl">No documents found. Start speaking to search.</div>
                )}

                {documents.map((doc: any, idx) => (
                  <div key={idx} className="bg-white border border-slate-200 rounded-2xl p-5 transition-all hover:shadow-lg hover:border-red-300 flex flex-col">
                    <div className="flex justify-between items-start mb-2">
                      <div className="flex-1 pr-4">
                        <h4 className="font-bold text-[15px] text-slate-800 leading-snug">{renderHighlight(doc.title, activeKeywords)}</h4>
                        <p className="text-[13px] text-slate-500 mt-2">{renderHighlight(doc.snippet || doc.about, activeKeywords)}</p>
                        <div className="text-[11px] text-slate-400 mt-3 font-medium flex items-center gap-2">
                          <span className="text-slate-700 font-bold bg-slate-100 px-2 py-0.5 rounded">{doc.source || "Unknown"}</span>
                          <span>•</span>
                          <span>Published: {doc.formattedDate || doc.rawDate || 'Unknown'}</span>
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-2 shrink-0">
                        {doc.metadata?.total_chapter && <span className="text-[10px] font-bold px-2.5 py-1 rounded border bg-indigo-50 text-indigo-700 border-indigo-200">{doc.metadata.total_chapter} Chapters</span>}
                        {doc.metadata?.total_section && <span className="text-[10px] font-bold px-2.5 py-1 rounded border bg-slate-50 text-slate-600 border-slate-200">{doc.metadata.total_section} Sections</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* VIEW: 2. PREVIEW / DEEP SEARCH */}
          {panelView === 'preview' && (
            <div className="flex flex-col animate-in fade-in slide-in-from-right-4 duration-300 pb-12">
              <Card title="Document Preview" subtitle="Detailed document structure and content">
                
                {/* Active Keywords Bar */}
                {activeKeywords.length > 0 && (
                  <div className="flex items-center gap-2 flex-wrap bg-yellow-50 border border-yellow-200/60 rounded-xl px-4 py-3 mb-6 text-xs text-yellow-800">
                    <Search size={14} className="text-yellow-600" />
                    <span className="font-medium mr-1">Highlighted Keywords:</span>
                    {activeKeywords.map((kw, i) => (
                      <span key={i} className="bg-yellow-200/50 border border-yellow-300/60 rounded-full px-2.5 py-0.5 font-bold tracking-wide">
                        {kw}
                      </span>
                    ))}
                  </div>
                )}

                {!docPreviewData ? (
                  <div className="text-center py-12 text-slate-400 text-sm animate-pulse">Loading document structure...</div>
                ) : (
                  <div className="flex flex-col gap-6">
                    
                    {/* Menggabungkan semua Header agar tampil dalam satu Card */}
                    {docPreviewData.structure?.filter((n: any) => n.type === 'header').length > 0 && (
                      <div className="bg-slate-50 border border-slate-200 rounded-xl p-8 text-center shadow-inner">
                        {docPreviewData.structure?.filter((n: any) => n.type === 'header').map((node: any, nIdx: number) => (
                          <div key={nIdx} className={nIdx > 0 ? "mt-5" : ""}>
                            {node.subtype === 'title' ? (
                              <h3 className="text-[15px] font-extrabold text-slate-900 leading-snug uppercase tracking-wide">
                                {renderHighlight(node.content, activeKeywords)}
                              </h3>
                            ) : (
                              <div>
                                <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400 mb-2">TENTANG</div>
                                <p className="text-sm font-medium text-slate-700 leading-relaxed">
                                  {renderHighlight(node.content, activeKeywords)}
                                </p>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {docPreviewData.structure?.filter((n: any) => n.type !== 'header').map((node: any, nIdx: number) => {
                      
                      // RENDER PREAMBLE
                      if (node.type === 'preamble') {
                        return (
                          <div key={nIdx} className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm text-sm text-slate-700 leading-loose">
                            {node.label && <h4 className="text-[11px] font-extrabold uppercase tracking-wider text-slate-500 mb-4">{node.label}</h4>}
                            {node.contents?.length ? (
                              <ul className="space-y-3">
                                {node.contents.map((item: any, iIdx: number) => {
                                  const contentStr = item.content || '';
                                  // Ekstrak awalan angka atau huruf list (misalnya "a." atau "1.")
                                  const match = contentStr.match(/^\s*([a-zA-Z0-9]+)\.\s*/);
                                  const bullet = match ? match[1] : item.point;
                                  const cleanContent = match ? contentStr.replace(/^\s*([a-zA-Z0-9]+)\.\s*/, '') : contentStr;
                                  
                                  return (
                                    <li key={iIdx} className="flex gap-3">
                                      {bullet && <span className="font-bold text-slate-900 shrink-0 min-w-[28px]">{bullet}.</span>}
                                      <span>{renderHighlight(cleanContent, activeKeywords)}</span>
                                    </li>
                                  );
                                })}
                              </ul>
                            ) : (
                              <p>{renderHighlight(node.content, activeKeywords)}</p>
                            )}
                          </div>
                        );
                      }

                      // RENDER BODY (Chapters, Sections, Verses)
                      if (node.type === 'body') {
                        return (
                          <div key={nIdx} className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm">
                            <div className="inline-block bg-blue-600 text-white rounded-md px-3 py-1 text-[11px] font-bold mb-5 shadow-sm">
                              CHAPTER {node.chapter_number} — {node.chapter_title}
                            </div>
                            
                            <div className="space-y-6">
                              {node.sections?.map((sec: any, sIdx: number) => (
                                <div key={sIdx}>
                                  <div className="text-xs font-extrabold text-slate-800 mb-2 tracking-wide">Section {sec.number}</div>
                                  <div className="space-y-2">
                                    {sec.contents?.map((verse: any, vIdx: number) => {
                                      // Hilangkan awalan angka duplikat jika ada (misal "1. ")
                                      const cleanContent = verse.content ? verse.content.replace(/^\s*\d+\.\s*/, '') : '';
                                      return (
                                        <div key={vIdx} className="text-[13px] text-slate-700 leading-relaxed flex gap-2">
                                          {verse.point && <span className="font-bold text-slate-900 shrink-0 min-w-[28px]">({verse.point})</span>}
                                          <span>{renderHighlight(cleanContent, activeKeywords)}</span>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        );
                      }
                      
                      return null;
                    })}
                  </div>
                )}
              </Card>
            </div>
          )}

          {/* VIEW: 3. DETAIL (SUMMARY & RELATIONS) */}
          {panelView === 'detail' && (
            <div className="flex flex-col animate-in fade-in slide-in-from-right-4 duration-300 pb-12">
              <Card title="Regulation Details" subtitle="Summary information and related regulations">
                
                {!docDetailData ? (
                  <div className="text-center py-12 text-slate-400 text-sm animate-pulse">Loading details...</div>
                ) : (
                  <div className="flex flex-col gap-6">
                    <div className="bg-slate-50 border border-slate-200 rounded-xl p-6 text-center shadow-inner mb-2">
                      <h3 className="text-[16px] font-bold text-slate-800 leading-snug tracking-tight mb-2">
                        {docDetailData.kind2 || "Document"}
                      </h3>
                      <p className="text-xs text-slate-500">
                        {docDetailData.judul || ""}
                      </p>
                    </div>

                    <div className="grid grid-cols-3 gap-3">
                      {[
                        { label: 'Chapters', value: docDetailData.total_bab },
                        { label: 'Sections', value: docDetailData.total_pasal },
                        { label: 'Verses', value: docDetailData.total_ayat }
                      ].map((stat, i) => (
                        <div key={i} className="bg-white border border-slate-200 rounded-xl p-4 text-center shadow-sm">
                          <div className="text-2xl font-black text-blue-600 tracking-tight">{stat.value || 0}</div>
                          <div className="text-[10px] uppercase font-bold tracking-widest text-slate-400 mt-1">{stat.label}</div>
                        </div>
                      ))}
                    </div>

                    <div className="space-y-4">
                      {[
                        { key: 'summary', label: 'General Summary' },
                        { key: 'summary_ruang_lingkup', label: 'Scope' },
                        { key: 'summary_tujuan', label: 'Objectives' },
                        { key: 'summary_tambahan', label: 'Additional Notes' }
                      ].map((sec, i) => {
                        if (!docDetailData[sec.key]) return null;
                        return (
                          <div key={i}>
                            <h4 className="text-[10px] font-extrabold uppercase tracking-widest text-slate-400 mb-2 ml-1">{sec.label}</h4>
                            <div className="bg-white border border-slate-200 rounded-xl p-5 text-[12px] leading-loose text-slate-700 shadow-sm">
                              {docDetailData[sec.key]}
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    {/* Relations */}
                    {(docDetailData.status_detail?.mencabut?.length > 0 || docDetailData.status_detail?.melaksanakan?.length > 0) && (
                      <div className="mt-2">
                        <h4 className="text-[10px] font-extrabold uppercase tracking-widest text-slate-400 mb-2 ml-1">Related Regulations</h4>
                        <div className="flex flex-col gap-2">
                          {docDetailData.status_detail?.mencabut?.map((r: string, i: number) => (
                            <div key={`m-${i}`} className="text-[11px] bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-slate-700 font-medium flex gap-2 items-center">
                              <span className="text-[9px] font-bold uppercase tracking-wider bg-red-100 text-red-700 px-2 py-0.5 rounded">Revokes</span>
                              {r}
                            </div>
                          ))}
                          {docDetailData.status_detail?.melaksanakan?.map((r: string, i: number) => (
                            <div key={`i-${i}`} className="text-[11px] bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-slate-700 font-medium flex gap-2 items-center">
                              <span className="text-[9px] font-bold uppercase tracking-wider bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded">Implements</span>
                              {r}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                  </div>
                )}

              </Card>
            </div>
          )}

          {/* VIEW: 4. NEWS */}
          {panelView === 'news' && (
             <div className="flex flex-col gap-4 animate-in fade-in duration-300">
                <Card title="Legal & Financial News" subtitle={`Search topic: ${searchQuery}`}>
                   {isLoading && newsArticles.length === 0 ? (
                      <div className="text-center py-6 text-slate-400 text-sm animate-pulse">Loading latest news...</div>
                   ) : (
                      newsArticles.length > 0 ? (
                        newsArticles.map((art, idx) => (
                          <div key={idx} className="mb-4 pb-4 border-b border-slate-100 last:border-0 last:mb-0 last:pb-0">
                             <h4 className="font-bold text-slate-800 text-sm leading-snug">{art.title}</h4>
                             <p className="text-[12px] text-slate-500 mt-1.5 line-clamp-3 leading-relaxed">{art.snippet}</p>
                             <div className="flex items-center gap-2 mt-3">
                                <span className="bg-cyan-50 text-cyan-700 text-[10px] font-bold px-2 py-0.5 rounded uppercase">{art.source || "News"}</span>
                                <span className="text-[11px] font-medium text-slate-400">{art.published}</span>
                                {art.url && <a href={art.url} target="_blank" rel="noreferrer" className="ml-auto text-blue-600 hover:text-blue-800 text-xs font-bold tracking-wide">Read →</a>}
                             </div>
                          </div>
                        ))
                      ) : (
                        <div className="text-center py-6 text-slate-400 text-sm">No news found.</div>
                      )
                   )}
                </Card>
             </div>
          )}

        </div>
      </div>
    </div>
  );
}