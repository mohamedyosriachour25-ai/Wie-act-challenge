import { useCallback, useEffect, useRef, useState } from "react";
import { api, wsUrl } from "./api.js";
import { useLang } from "./i18n.jsx";
import { ATTENTION, createTracker, loadDetector } from "./attention.js";

/** Poll the AI gateway status. Fast while a request is running, slow otherwise. */
export function useAIStatus() {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    let alive = true;
    let timer;
    const tick = async () => {
      try {
        const s = await api("/api/ai/status");
        if (alive) setStatus(s);
        timer = setTimeout(tick, s.state === "idle" ? 4000 : 1000);
      } catch {
        timer = setTimeout(tick, 6000);
      }
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  return status;
}

/** One WebSocket per room, with the server clock offset so every client shows the same timer. */
export function useRoomSocket(code, onEvent) {
  const [conn, setConn] = useState("connecting"); // connecting | open | closed
  const [error, setError] = useState(null);
  const wsRef = useRef(null);
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const offset = useRef(0); // serverNow - clientNow, seconds
  const { lang } = useLang();

  useEffect(() => {
    let closedByUs = false;
    const ws = new WebSocket(wsUrl(code));
    wsRef.current = ws;
    ws.onopen = () => setConn("open");
    ws.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.server_now) offset.current = msg.server_now - Date.now() / 1000;
      if (msg.type === "error") setError(msg);
      handler.current?.(msg);
    };
    ws.onclose = () => !closedByUs && setConn("closed");
    return () => {
      closedByUs = true;
      ws.close();
    };
  }, [code]);

  // Tell the room which language this person reads, so server messages and the AI answer them in it.
  useEffect(() => {
    if (conn === "open") wsRef.current?.send(JSON.stringify({ type: "lang", lang }));
  }, [lang, conn]);

  const send = useCallback((payload) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(payload));
  }, []);
  const now = useCallback(() => Date.now() / 1000 + offset.current, []);
  return { conn, error, send, now };
}

/** Phase of a repeating focus/break cycle, from the shared timer definition. */
export function useCycle(timer, now) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 500);
    return () => clearInterval(id);
  }, []);
  if (!timer) return null;
  const total = timer.focus_s + timer.break_s;
  const pos = (now() - timer.started_at) % total;
  const inFocus = pos < timer.focus_s;
  const length = inFocus ? timer.focus_s : timer.break_s;
  const into = inFocus ? pos : pos - timer.focus_s;
  const cycles = Math.floor((now() - timer.started_at) / total);
  return { phase: inFocus ? "focus" : "break", remaining: Math.max(0, Math.ceil(length - into)), length, into, cycles };
}

/**
 * Focus signal computed entirely on this device: tab visibility, window focus and recent input.
 * `camera` is the optional verdict of useAttention (true = facing the screen, false = away, null = no
 * camera information). The contract is unchanged: a single boolean is all that ever leaves the device.
 *  - camera === false  -> not focused (the learner has left or looked away for a while)
 *  - camera === true   -> the input-idle rule is waived (reading a book or notes is still studying)
 * `opts.idleMs` sets how long without input counts as idle (default 90 s; the study forest passes Infinity
 * because there only the tab matters).
 */
export function useFocusSignal(enabled, camera = null, opts = {}) {
  const [focused, setFocused] = useState(null);
  const cam = useRef(camera);
  cam.current = camera;
  const idleMs = useRef(opts.idleMs ?? 90_000);
  idleMs.current = opts.idleMs ?? 90_000;
  const recompute = useRef(null);
  useEffect(() => {
    if (!enabled) {
      recompute.current = null;
      setFocused(null);
      return;
    }
    let lastInput = Date.now();
    const compute = () => {
      const idle = Date.now() - lastInput > idleMs.current;
      const away = cam.current === false;
      const seen = cam.current === true;
      setFocused(document.visibilityState === "visible" && document.hasFocus() && !away && (seen || !idle));
    };
    recompute.current = compute;
    const touch = () => {
      lastInput = Date.now();
    };
    const events = ["pointermove", "keydown", "scroll", "pointerdown"];
    events.forEach((n) => window.addEventListener(n, touch, { passive: true }));
    document.addEventListener("visibilitychange", compute);
    window.addEventListener("focus", compute);
    window.addEventListener("blur", compute);
    const id = setInterval(compute, 5000);
    compute();
    return () => {
      events.forEach((n) => window.removeEventListener(n, touch));
      document.removeEventListener("visibilitychange", compute);
      window.removeEventListener("focus", compute);
      window.removeEventListener("blur", compute);
      clearInterval(id);
      recompute.current = null;
    };
  }, [enabled]);
  useEffect(() => { recompute.current?.(); }, [camera]); // a new camera verdict applies immediately
  return focused;
}

let detectorPromise = null; // the face model is loaded once per page session, only after opt-in

/**
 * Opt-in camera attention check, fully on-device. While `enabled && active` it opens the camera (video
 * only, 320x240), looks at ~2 frames per second and reduces them to one verdict. Frames are never stored,
 * drawn or sent anywhere. When `active` turns false (e.g. during the break) the camera is released.
 * Returns { status, attentive, reason, backend }:
 *   status: off | paused | loading | running | denied | unsupported | error
 *   attentive: null (no verdict yet / not running) | true | false;  reason: starting | ok | no_face | turned
 */
export function useAttention(enabled, active = true) {
  const idle = { attentive: null, reason: "starting", backend: null };
  const [state, setState] = useState({ status: "off", ...idle });
  useEffect(() => {
    if (!enabled || !active) {
      setState((s) => ({ status: enabled ? "paused" : "off", ...idle, backend: s.backend }));
      return undefined;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setState({ status: "unsupported", ...idle });
      return undefined;
    }
    let alive = true;
    let stream = null;
    let video = null;
    let timer = null;
    const tracker = createTracker();
    const release = () => {
      stream?.getTracks().forEach((tr) => tr.stop());
      stream = null;
      if (video) video.srcObject = null;
    };
    (async () => {
      setState((s) => ({ status: "loading", ...idle, backend: s.backend }));
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240, facingMode: "user" }, audio: false });
      } catch {
        if (alive) setState({ status: "denied", ...idle });
        return;
      }
      if (!alive) return release();
      let detector;
      try {
        detector = await (detectorPromise ||= loadDetector());
      } catch {
        detectorPromise = null;
        release();
        if (alive) setState({ status: "error", ...idle });
        return;
      }
      if (!alive) return release();
      video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.srcObject = stream;
      await video.play().catch(() => {});
      setState({ status: "running", ...idle, backend: detector.backend });
      const loop = async () => {
        if (!alive) return;
        const t0 = performance.now();
        if (document.visibilityState === "visible" && video.readyState >= 2 && video.videoWidth) {
          try {
            const v = tracker.update({ t: performance.now(), face: await detector.analyse(video) });
            if (alive) setState((s) => (s.status === "running" && s.attentive === v.attentive && s.reason === v.reason ? s : { status: "running", attentive: v.attentive, reason: v.reason, backend: detector.backend }));
          } catch { /* one bad frame is skipped */ }
        } else {
          tracker.pause();
        }
        if (alive) timer = setTimeout(loop, Math.max(100, ATTENTION.sampleMs - (performance.now() - t0)));
      };
      loop();
    })();
    return () => {
      alive = false;
      clearTimeout(timer);
      release();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, active]);
  return state;
}

export const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

/**
 * Camera wall for a Live room: a small peer-to-peer mesh over WebRTC. The video never touches the
 * server — only the SDP/ICE signalling is relayed through the room socket (see `send`/`signal`).
 * Uses the "perfect negotiation" pattern so two browsers can offer at the same time without clashing.
 */
const ICE = [{ urls: "stun:stun.l.google.com:19302" }, { urls: "stun:stun1.l.google.com:19302" }];

export function useVideoMesh(send, myId) {
  const [cameraOn, setCameraOn] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);
  const [tiles, setTiles] = useState([]); // remote tiles: { id, pseudo, stream }
  const [localStream, setLocalStream] = useState(null);
  const [watching, setWatching] = useState(false); // in the mesh to receive video, without sharing a camera

  const joined = useRef(false);
  const peers = useRef(new Map()); // id -> { pc, pseudo, polite, makingOffer, ignoreOffer }
  const local = useRef(null);
  const sendRef = useRef(send); sendRef.current = send;
  const idRef = useRef(myId); idRef.current = myId;

  const refreshTiles = useCallback(() => {
    setTiles([...peers.current.entries()].map(([id, p]) => ({ id, pseudo: p.pseudo, stream: p.stream })).filter((t) => t.stream));
  }, []);

  const rtc = useCallback((to, data) => sendRef.current({ type: "rtc_signal", to, data }), []);

  const ensurePeer = useCallback((id, pseudo) => {
    if (id === idRef.current || peers.current.has(id)) return peers.current.get(id);
    const pc = new RTCPeerConnection({ iceServers: ICE });
    const state = { pc, pseudo, polite: idRef.current > id, makingOffer: false, ignoreOffer: false, stream: null };
    peers.current.set(id, state);
    local.current?.getTracks().forEach((track) => pc.addTrack(track, local.current));
    pc.ontrack = (e) => { state.stream = e.streams[0]; refreshTiles(); };
    pc.onicecandidate = ({ candidate }) => candidate && rtc(id, { candidate });
    pc.onnegotiationneeded = async () => {
      try {
        state.makingOffer = true;
        await pc.setLocalDescription();
        rtc(id, { desc: pc.localDescription });
      } catch { /* renegotiation races are expected; perfect negotiation recovers */ } finally {
        state.makingOffer = false;
      }
    };
    pc.onconnectionstatechange = () => { if (pc.connectionState === "failed") pc.restartIce?.(); };
    return state;
  }, [rtc, refreshTiles]);

  const dropPeer = useCallback((id) => {
    const p = peers.current.get(id);
    if (p) { try { p.pc.close(); } catch { /* already closed */ } peers.current.delete(id); refreshTiles(); }
  }, [refreshTiles]);

  // Feed every rtc_* message from the room socket in here.
  const signal = useCallback(async (m) => {
    if (m.type === "rtc_full") { stop(); setError({ message: "full", retryAfter: m.capacity }); return; }
    if (m.type === "rtc_peers") { m.peers.forEach((p) => ensurePeer(p.id, p.pseudo)); return; }
    if (m.type === "rtc_peer_join") { ensurePeer(m.id, m.pseudo); return; }
    if (m.type === "rtc_peer_leave") { dropPeer(m.id); return; }
    if (m.type !== "rtc_signal") return;
    const state = peers.current.get(m.from);
    if (!state) return;
    const { pc } = state;
    try {
      if (m.data.desc) {
        const offer = m.data.desc.type === "offer";
        const collision = offer && (state.makingOffer || pc.signalingState !== "stable");
        state.ignoreOffer = !state.polite && collision;
        if (state.ignoreOffer) return;
        await pc.setRemoteDescription(m.data.desc);
        if (offer) { await pc.setLocalDescription(); rtc(m.from, { desc: pc.localDescription }); }
      } else if (m.data.candidate) {
        try { await pc.addIceCandidate(m.data.candidate); } catch { if (!state.ignoreOffer) throw new Error("ice"); }
      }
    } catch { /* a dropped peer or a late candidate: safe to ignore */ }
  }, [ensurePeer, dropPeer, rtc]);

  function stop() {
    peers.current.forEach((p) => { try { p.pc.close(); } catch { /* noop */ } });
    peers.current.clear();
    local.current?.getTracks().forEach((t) => t.stop());
    local.current = null;
    joined.current = false;
    setLocalStream(null);
    setTiles([]);
    setCameraOn(false);
    setWatching(false);
  }

  const start = useCallback(async (audio = false) => {
    setError(null);
    setStarting(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240 }, audio });
      local.current = stream;
      setLocalStream(stream);
      setCameraOn(true);
      setWatching(false);
      // Already watching: hand the new tracks to the open connections (they renegotiate on their own).
      peers.current.forEach(({ pc }) => stream.getTracks().forEach((track) => pc.addTrack(track, stream)));
      joined.current = true;
      sendRef.current({ type: "rtc_join" });
    } catch {
      setError({ message: "denied" });
    } finally {
      setStarting(false);
    }
  }, []);

  const toggle = useCallback(() => {
    if (cameraOn) { sendRef.current({ type: "rtc_leave" }); stop(); } else { start(false); }
  }, [cameraOn, start]);

  const perform = useCallback(() => { if (!cameraOn) start(true); }, [cameraOn, start]);       // go on stage: camera + mic
  const leaveStage = useCallback(() => { if (cameraOn) { sendRef.current({ type: "rtc_leave" }); stop(); } }, [cameraOn]);

  // Watch only: see the others' cameras (a mime performer) without turning yours on.
  const watch = useCallback(() => {
    if (joined.current) return;
    joined.current = true;
    setWatching(true);
    sendRef.current({ type: "rtc_join", watch: true });
  }, []);
  const unwatch = useCallback(() => {
    if (!joined.current || local.current) return;   // sharing a camera: leave that to toggle / leaveStage
    sendRef.current({ type: "rtc_leave" });
    stop();
  }, []);

  useEffect(() => () => { sendRef.current?.({ type: "rtc_leave" }); stop(); }, []); // leave on unmount
  return { cameraOn, starting, error, tiles, localStream, watching, toggle, perform, leaveStage, watch, unwatch, signal };
}
