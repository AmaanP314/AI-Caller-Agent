// --- DOM Elements ---
const sessionIdInput = document.getElementById("session-id");
const getInfoBtn = document.getElementById("get-info-btn");
const endCallDbBtn = document.getElementById("end-call-db-btn");
const startCallBtn = document.getElementById("start-call-btn");
const hangUpBtn = document.getElementById("hang-up-btn");
const chatLog = document.getElementById("chat-log");
const agentState = document.getElementById("agent-state");
const wsStatusLight = document.getElementById("ws-status");
const wsStatusText = document.getElementById("ws-status-text");
const micStatusLight = document.getElementById("mic-status");
const micStatusText = document.getElementById("mic-status-text");
const agentStatusLight = document.getElementById("agent-status");
const agentStatusText = document.getElementById("agent-status-text");

// --- State ---
let webSocket = null;
let audioContext = null;
let audioWorkletNode = null;
let mediaStreamSource = null;
let audioPlaybackQueue = [];
let isPlaybackRunning = false;
let isCallActive = false;
let agentIsSpeaking = false;

// --- Config ---
// const SERVER_HOST = window.location.host;
// const WEBSOCKET_URL = `ws://${SERVER_HOST}/ws/vicidial/`;
// const HTTP_URL = `http://${SERVER_HOST}/api/`;
const WEBSOCKET_URL = `wss://8000-dep-01k9272p639md822bpqr840709-d.cloudspaces.litng.ai/ws/vicidial/`;
const HTTP_URL = `https://8000-dep-01k9272p639md822bpqr840709-d.cloudspaces.litng.ai/api/`;

// --- Initialization ---
sessionIdInput.value = "browser-test-" + Date.now();
endCallDbBtn.addEventListener("click", endCallForDB);
getInfoBtn.addEventListener("click", getPatientInfo);
startCallBtn.addEventListener("click", startCall);
hangUpBtn.addEventListener("click", hangUp);

// --- Core Functions ---

async function startCall() {
  if (isCallActive) return;

  const sessionId = sessionIdInput.value;
  if (!sessionId) {
    alert("Please enter a Session ID");
    return;
  }

  setCallButtonState(true);
  addLogMessage("system", "Starting call...");

  try {
    // 1. Initialize Audio
    await setupAudioPipeline();

    // 2. Initialize WebSocket
    await setupWebSocket(sessionId);

    isCallActive = true;
  } catch (error) {
    console.error("Error starting call:", error);
    addLogMessage("system", `Error: ${error.message}`);
    setCallButtonState(false);
    await cleanup(); // Ensure everything is reset
  }
}

async function hangUp() {
  if (!isCallActive) return;

  addLogMessage("system", "Hanging up...");

  if (webSocket && webSocket.readyState === WebSocket.OPEN) {
    webSocket.send(JSON.stringify({ type: "hangup" }));
  }

  await cleanup();
  setCallButtonState(false);
}

async function cleanup() {
  isCallActive = false;

  if (webSocket) {
    webSocket.close(1000, "User hung up");
    webSocket = null;
  }

  if (audioWorkletNode) {
    audioWorkletNode.disconnect();
    audioWorkletNode = null;
  }
  if (mediaStreamSource) {
    mediaStreamSource.disconnect();
    mediaStreamSource = null;
  }
  if (audioContext && audioContext.state !== "closed") {
    await audioContext.close();
    audioContext = null;
  }

  // Reset playback queue
  audioPlaybackQueue = [];
  isPlaybackRunning = false;

  setMicStatus("off");
  setWsStatus("off", "Disconnected");
  setAgentStatus("idle");
}

// --- Audio Pipeline (Mic -> Worklet -> WebSocket) ---

async function setupAudioPipeline() {
  try {
    // 1. Get mic permissions
    setMicStatus("yellow", "Requesting...");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        // Request a sample rate the server expects (16k),
        // but the worklet will resample anyway.
        sampleRate: 16000,
        channelCount: 1,
      },
    });

    // 2. Create AudioContext and load worklet
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    await audioContext.audioWorklet.addModule("js/audio-worklet.js");

    // 3. Create nodes
    mediaStreamSource = audioContext.createMediaStreamSource(stream);
    audioWorkletNode = new AudioWorkletNode(audioContext, "audio-processor");

    // 4. Set up message listener (Worklet -> app.js)
    audioWorkletNode.port.onmessage = (event) => {
      // event.data is Int16Array (16-bit PCM)
      sendAudioChunk(event.data);
    };

    // 5. Connect graph: Mic -> Worklet
    mediaStreamSource.connect(audioWorkletNode);

    setMicStatus("green", "Mic Active");
  } catch (error) {
    console.error("Failed to initialize audio pipeline:", error);
    setMicStatus("red", "Mic Error");
    throw error;
  }
}

function sendAudioChunk(int16PcmData) {
  if (webSocket && webSocket.readyState === WebSocket.OPEN) {
    // We send raw bytes as base64
    const base64Data = btoa(
      String.fromCharCode.apply(null, new Uint8Array(int16PcmData.buffer))
    );
    webSocket.send(
      JSON.stringify({
        type: "audio_data",
        audio: base64Data,
        format: "pcm16k", // Tell server this is 16kHz PCM
      })
    );
  }
}

// --- WebSocket Management ---

function setupWebSocket(sessionId) {
  return new Promise((resolve, reject) => {
    webSocket = new WebSocket(WEBSOCKET_URL + sessionId);

    setWsStatus("yellow", "Connecting...");

    webSocket.onopen = (event) => {
      setWsStatus("green", "Connected");
      addLogMessage("system", "WebSocket connection established.");
      resolve();
    };

    webSocket.onmessage = (event) => {
      handleWsMessage(JSON.parse(event.data));
    };

    webSocket.onclose = (event) => {
      setWsStatus("red", "Closed");
      addLogMessage(
        "system",
        `WebSocket closed: ${event.reason} (Code: ${event.code})`
      );
      if (isCallActive) {
        // Unexpected close
        hangUp();
      }
    };

    webSocket.onerror = (error) => {
      console.error("WebSocket Error:", error);
      setWsStatus("red", "Error");
      addLogMessage("system", "WebSocket error.");
      reject(error);
    };
  });
}

function handleWsMessage(msg) {
  switch (msg.type) {
    case "audio_response":
      // Received a chunk of audio (WAV) from the agent
      if (!agentIsSpeaking) {
        setAgentStatus("speaking");
        agentIsSpeaking = true;
      }
      // Add to playback queue
      queueAudioPlayback(msg.audio);
      break;

    case "transcript":
      // Received a user transcript
      addLogMessage("user", msg.text);
      break;

    // You could add other custom message types here
    // e.g., case 'agent_status':
    //          setAgentStatus(msg.status);
    //          break;
  }
}

// --- Audio Playback (WebSocket -> AudioContext) ---

function queueAudioPlayback(base64WavData) {
  // 1. Convert base64 to ArrayBuffer
  const binaryString = atob(base64WavData);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  // 2. Add buffer to queue
  audioPlaybackQueue.push(bytes.buffer);

  // 3. If playback is not already running, start it
  if (!isPlaybackRunning) {
    playNextInQueue();
  }
}

async function playNextInQueue() {
  if (audioPlaybackQueue.length === 0) {
    isPlaybackRunning = false;
    // Only set agent to idle if we've finished playing all chunks
    setAgentStatus("idle");
    agentIsSpeaking = false;
    return;
  }

  isPlaybackRunning = true;
  const audioBuffer = audioPlaybackQueue.shift();

  try {
    // Use the same AudioContext to play back
    if (!audioContext) {
      console.warn("AudioContext not ready, skipping playback.");
      return;
    }

    // Decode the WAV data
    const decodedBuffer = await audioContext.decodeAudioData(audioBuffer);

    // Create a source, connect it, and start
    const source = audioContext.createBufferSource();
    source.buffer = decodedBuffer;
    source.connect(audioContext.destination);

    // Set an 'onended' event to play the next chunk
    source.onended = playNextInQueue;
    source.start();
  } catch (error) {
    console.error("Error decoding or playing audio:", error);
    // Skip this chunk and try the next
    playNextInQueue();
  }
}

// --- HTTP API Functions (for side panel) ---

async function getPatientInfo() {
  const sessionId = sessionIdInput.value;
  if (!sessionId) {
    alert("Please enter a Session ID");
    return;
  }

  try {
    const response = await fetch(`${HTTP_URL}patient-info/${sessionId}`);
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Info not found or error: ${errorText}`);
    }
    const data = await response.json();
    agentState.textContent = JSON.stringify(data.patient_info, null, 2);
  } catch (error) {
    console.error("Error getting info:", error);
    agentState.textContent = `Error: ${error.message}`;
  }
}

async function endCallForDB() {
  const sessionId = sessionIdInput.value;
  if (!sessionId) {
    alert("Please enter a Session ID");
    return;
  }

  if (isCallActive) {
    addLogMessage("system", 'Call is active. Please "Hang Up" first.');
    return;
  }

  if (
    !confirm(
      "This will end and save the call log for the current Session ID. OK?"
    )
  ) {
    return;
  }

  try {
    const response = await fetch(`${HTTP_URL}end-call/${sessionId}`, {
      method: "POST",
    });
    if (!response.ok) throw new Error("Failed to end call");
    const data = await response.json();
    addLogMessage(
      "system",
      `--- Call manually saved to DB (Status: ${data.status}) ---`
    );
    // Clear UI
    agentState.textContent = "{}";
    chatLog.innerHTML = "";
    sessionIdInput.value = "browser-test-" + Date.now();
  } catch (error) {
    console.error("Error ending call:", error);
    addLogMessage("system", `Error saving call: ${error.message}`);
  }
}

// --- UI Utility Functions ---

function addLogMessage(role, text) {
  const msgDiv = document.createElement("div");
  msgDiv.classList.add("message", role);
  msgDiv.textContent = text;
  chatLog.appendChild(msgDiv);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setCallButtonState(isCalling) {
  startCallBtn.disabled = isCalling;
  hangUpBtn.disabled = !isCalling;
}

function setWsStatus(status, text) {
  wsStatusLight.className = `status-light ${
    status === "green" ? "green" : status === "yellow" ? "yellow" : "red"
  }`;
  wsStatusText.textContent = text;
}

function setMicStatus(status, text) {
  micStatusLight.className = `status-light ${
    status === "green" ? "green" : status === "yellow" ? "yellow" : "red"
  }`;
  micStatusText.textContent = text;
}

function setAgentStatus(status) {
  if (status === "speaking") {
    agentStatusLight.className = "status-light green";
    agentStatusText.textContent = "Agent Speaking";
  } else {
    agentStatusLight.className = "status-light";
    agentStatusText.textContent = "Agent Idle";
  }
}
