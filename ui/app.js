/* JARVIS Live HUD — drives Claude Code, streams its telemetry.

   The brain is your own `claude` CLI (Path A), so whatever MCP connectors and
   skills you've configured are live. This file only streams and renders. */
const $ = s => document.querySelector(s);
const RT = {tts:false, stt:false};

// ── reactor ticks ──
(() => {
  const g = $('#ticks480');
  for (let i = 0; i < 96; i++){
    const a = (i/96) * Math.PI*2, maj = i % 8 === 0;
    const r0 = maj ? 226 : 230, r1 = 238;
    const l = document.createElementNS('http://www.w3.org/2000/svg','line');
    l.setAttribute('x1', 240+Math.cos(a)*r0); l.setAttribute('y1', 240+Math.sin(a)*r0);
    l.setAttribute('x2', 240+Math.cos(a)*r1); l.setAttribute('y2', 240+Math.sin(a)*r1);
    if (maj) l.setAttribute('class','maj');
    g.appendChild(l);
  }
})();

const now = () => new Date().toLocaleTimeString('en-GB', {hour12:false});
const rid = () => 'run_' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g,'').slice(0,24)
                                              : Math.random().toString(16).slice(2,14));
const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

// ── reactor state ──
function setState(cls, word, sub){
  document.body.className = cls;
  if (word) $('#stword').textContent = word;
  if (sub != null) $('#strun').textContent = sub;
}
function setTag(cls, text){ const t=$('#rtag'); t.className='rtag '+cls; t.textContent=text; }

// ── action log ──
function log(kind, label, msg, jsonHtml){
  const e = document.createElement('div');
  e.className = 'entry k-' + kind;
  e.innerHTML = `<div class="top"><span class="ts">${now()}</span>`
    + `<span class="kind">${esc(label)}</span></div>`
    + (msg ? `<div class="msg">${esc(msg)}</div>` : '')
    + (jsonHtml ? `<div class="jsonblk">${jsonHtml}</div>` : '');
  const box = $('#log');
  box.insertBefore(e, box.firstChild);
  while (box.children.length > 40) box.removeChild(box.lastChild);
}
function usageBlock(u){
  const row = (k,v) => `  <span class="k">"${k}"</span>: <span class="v">${v}</span>`;
  return `{\n  <span class="k">"usage"</span>: {\n`
    + [row('input_tokens', u.input_tokens), row('output_tokens', u.output_tokens),
       row('total_tokens', u.total_tokens)].join(',\n')
    + `\n  }\n}`;
}

// ── the run ──
let running = false, answer = '', firstDelta = false, speakDone = Promise.resolve();

const STANDBY_RE = /^\W*(hey\W+)?(jarvis\W+)?(please\W+|can you\W+|okay\W+)?(((go|get|be)\s+(on|to|in)\s+)?stand?\s?-?by(\s+mode)?|go\s+to\s+(bed|sleep)|good\s?night)(\W+now)?(\W+please)?(\W+jarvis)?\W*$/i;
// JARVIS's own reply to a standby request: "On standby.", "Okay, I'm on standby. Say …"
const STANDBY_ANS = /^\W*((okay|ok|alright|sure|got it|you got it|goodnight|good night)\W+)*(i'?m\s+)?(on\s+standby|standing\s+by|going\s+(on|to)\s+standby)\b/i;

function showStandby(say){
  setState('', 'STANDBY', 'awaiting uplink');
  setTag('', 'IDLE');
  $('#response').innerHTML = '<span class="ph">On standby.</span>';
  log('note', 'STANDBY', 'on standby — say "Hey JARVIS" when you need me');
  if (say) speakDone = speak('On standby.');
}

async function transmit(message){
  if (!message.trim()) return;
  if (STANDBY_RE.test(message.trim())){ showStandby(true); return; }
  if (running){
    // don't silently swallow it — say so, so it never looks like nothing happened
    log('note','BUSY',`still working — "${message.slice(0,40)}" not sent. Wait, or press Esc to cancel.`);
    return;
  }
  running = true; answer = ''; firstDelta = false;

  const fresh = /^\/new\b/.test(message.trim());
  const id = rid();
  setState('running', 'RUNNING', id.slice(0,20) + '…');
  setTag('run', 'RUNNING');
  $('#response').innerHTML = '<span class="cur"></span>';
  log('run', 'RUN', `started ${id}; uplink cleared`);

  // visible elapsed counter — a slow answer should never look like a dead screen
  const t0 = performance.now();
  const tick = setInterval(() => {
    if (!running) return;
    const s = ((performance.now()-t0)/1000).toFixed(1);
    if (!answer) $('#strun').textContent = `thinking… ${s}s`;
  }, 200);

  try {
    const res = await fetch('/api/run', {
      method:'POST', headers:{'content-type':'application/json'},
      body: JSON.stringify({message, fresh})
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true){
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream:true});
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) handle(JSON.parse(line));
      }
    }
  } catch (e){
    log('error', 'ERROR', String(e).slice(0,200));
    setState('error', 'FAULT', 'stream dropped');
    setTag('err', 'ERROR');
  }
  running = false;
  clearInterval(tick);
  await speakDone;                 // don't return until Jarvis has finished talking
}

function handle(ev){
  switch (ev.t){
    case 'status':
      if (ev.session_id) sys('session', ev.session_id.slice(0,8));
      if (ev.mcp) setChips(ev.mcp);
      log('status', 'STATUS',
          `core online · tools=${ev.tools} `
          + `mcp=[${(ev.mcp||[]).join(', ')||'—'}] permission=${ev.permission}`);
      break;
    case 'latency':
      log('latency', 'LATENCY', `first token after ${ev.ms}ms`);
      break;
    case 'tool':
      if (ev.phase === 'use')
        log('tool', 'TOOL', `→ ${ev.name}(${ev.input || ''})`);
      else
        log('tool', 'TOOL', `✓ ${ev.ok===false?'error':'ok'}`);
      break;
    case 'delta':
      answer += ev.text;
      renderAnswer();
      if (!firstDelta){
        firstDelta = true;
        log('reason', 'REASONING', answer.slice(0, 220));
      }
      break;
    case 'usage':
      log('status', 'COMPLETE', 'run completed', usageBlock(ev));
      break;
    case 'complete':
      setState('done', 'COMPLETE', (ev.ms!=null?`${ev.ms}ms`:'') );
      setTag('done', 'COMPLETE');
      log('complete', 'COMPLETE', ev.ms!=null?`run completed in ${ev.ms}ms`:'run completed');
      renderAnswer(true);
      speakDone = speak(answer);         // transmit() awaits this before re-listening
      if (STANDBY_ANS.test(answer.trim()))
        setState('', 'STANDBY', 'awaiting uplink');
      break;
    case 'error':
      setState('error', 'FAULT', ev.message?.slice(0,40) || 'error');
      setTag('err', 'ERROR');
      log('error', 'ERROR', ev.message || 'unknown');
      // show it in the panel too, so a failure isn't a silent empty box
      if (!answer) $('#response').innerHTML =
        `<span style="color:var(--red)">⚠ ${esc(ev.message || 'Run failed.')}</span>`;
      else renderAnswer(true);
      break;
    case 'note':
      log('note', 'NOTE', ev.message || '');
      break;
  }
}

function renderAnswer(finaldone){
  const el = $('#response');
  el.innerHTML = esc(answer) + (finaldone ? '' : '<span class="cur"></span>');
  el.scrollTop = el.scrollHeight;
}

/* ══════════ continuous voice conversation ══════════
   Click VOICE once. From then on: listen → you stop → transcribe → Jarvis
   answers and speaks → it listens again, automatically. No re-clicking between
   turns. Click VOICE again (or press Esc) to end the conversation.

   The mic is deliberately DEAF while Jarvis is thinking or speaking (`suppress`)
   — otherwise it transcribes his own voice through the speakers and talks to
   itself forever. That's why re-arming happens only after he finishes. */
let convo = false, suppress = false;
let micStream=null, recorder=null, chunks=[], actx=null, analyser=null, vdata=null;
let vad=null, spoke=false, loudAt=0, turnStart=0;
let floorSum=0, floorN=0, threshold=0.02, peak=0, calibrating=true;
const SILENCE=900, MIN_TURN_MS=350, MAX_TURN_MS=18000, NO_SPEECH_MS=7000;

// ── voice out ──
let muted = false, player = null, ttsDown = false;

/* Fallback voice: the browser's own speech synthesis. Free, unlimited, always
   available. Used whenever ElevenLabs can't deliver — out of quota, network
   blip, bad key — so Jarvis is never silently mute. */
function browserSpeak(text){
  return new Promise(resolve => {
    if (!window.speechSynthesis) return resolve();
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const v = speechSynthesis.getVoices();
    u.voice = v.find(x => /daniel|arthur|george|british/i.test(x.name))
           || v.find(x => x.lang === 'en-GB') || v[0];
    u.rate = 1.05; u.pitch = 0.95;
    document.body.classList.add('speaking');
    const done = () => { document.body.classList.remove('speaking'); resolve(); };
    u.onend = done; u.onerror = done;
    speechSynthesis.speak(u);
    // Safety net: some browsers never fire onend for long strings.
    setTimeout(() => { if (speechSynthesis.speaking) return; done(); },
               Math.min(30000, 900 + text.length * 70));
  });
}

async function speak(text){
  text = (text||'').trim();
  if (muted || !text) return;
  suppress = true;                                    // go deaf before audio starts
  const spoken = text.length > 700 ? text.slice(0, 680).replace(/\s+\S*$/,'') + '…' : text;

  if (RT.tts && !ttsDown){
    try {
      const r = await fetch('/api/speak', {
        method:'POST', headers:{'content-type':'application/json'},
        body: JSON.stringify({text: spoken})});
      if (!r.ok){
        // read the reason instead of throwing it away
        let why = `HTTP ${r.status}`;
        try { why = (await r.json()).error || why; } catch(_){}
        throw new Error(why);
      }
      const url = URL.createObjectURL(await r.blob());
      if (player) player.pause();
      player = new Audio(url);
      document.body.classList.add('speaking');
      await new Promise((res, rej) => {
        player.onended = () => { URL.revokeObjectURL(url); res(); };
        player.onerror = () => { URL.revokeObjectURL(url); rej(new Error('playback failed')); };
        player.play().catch(rej);                     // autoplay block lands here
      });
      document.body.classList.remove('speaking');
      return;
    } catch (e){
      const why = String(e.message || e);
      const quota = /quota|401|429|unusual activity|exceed/i.test(why);
      if (quota){
        ttsDown = true;                               // stop retrying every turn
        sys('voice', 'quota spent', 'warn');
        $('#p-voice').textContent = 'Voice: ElevenLabs quota spent — browser voice';
        log('error','VOICE', `ElevenLabs out of characters — switching to the browser voice. (${why.slice(0,90)})`);
      } else {
        log('error','VOICE', `ElevenLabs failed (${why.slice(0,90)}) — using browser voice.`);
      }
      document.body.classList.remove('speaking');
    }
  }

  await browserSpeak(spoken);                         // always say something
}

// ── start / stop the whole conversation ──
async function micToggle(){
  if (convo){ stopConvo(); return; }
  if (!RT.stt){ log('error','VOICE','no ElevenLabs key — set one in .env'); return; }
  const opts = {echoCancellation:true, noiseSuppression:true, autoGainControl:true};
  try {
    micStream = await navigator.mediaDevices.getUserMedia({audio:opts});
    // The browser's default mic is often not the one you're talking into.
    // Open with ?mic=<part of its name> once; the choice is remembered.
    const want = new URLSearchParams(location.search).get('mic');
    try { if (want !== null) localStorage.setItem('jarvis.mic', want); } catch(_){}
    let pref = want; try { pref = pref ?? localStorage.getItem('jarvis.mic'); } catch(_){}
    const mics = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind==='audioinput');
    const pick = pref && mics.find(d => d.label.toLowerCase().includes(pref.toLowerCase()));
    if (pick){
      micStream.getTracks().forEach(t=>t.stop());
      micStream = await navigator.mediaDevices.getUserMedia({audio:{...opts, deviceId:{exact:pick.deviceId}}});
    } else if (pref){
      log('error','VOICE',`no mic matching "${pref}" — using the default`);
    }
    log('voice','VOICE',`mic in use: ${micStream.getAudioTracks()[0]?.label || 'unknown'}`);
    log('voice','VOICE',`mics available: ${mics.map(d=>d.label).join(' · ')}`);
  } catch(e){ log('error','VOICE','mic blocked — allow it in the address bar'); return; }
  actx = new (window.AudioContext||window.webkitAudioContext)();
  await actx.resume();
  analyser = actx.createAnalyser(); analyser.fftSize = 1024;
  actx.createMediaStreamSource(micStream).connect(analyser);
  vdata = new Uint8Array(analyser.fftSize);
  convo = true; suppress = false;
  $('#mic').classList.add('on'); $('#mic').textContent = '● LIVE';
  log('voice','VOICE','conversation open · listening');
  beginTurn();
}

function stopConvo(){
  convo = false; suppress = false;
  if (vad){ clearInterval(vad); vad=null; }
  if (recorder && recorder.state==='recording'){ recorder._cancel=true; try{recorder.stop();}catch(_){} }
  recorder = null;
  if (player){ try{player.pause();}catch(_){} }
  if (micStream){ micStream.getTracks().forEach(t=>t.stop()); micStream=null; }
  if (actx){ actx.close().catch(()=>{}); actx=null; analyser=null; }
  document.body.classList.remove('speaking');
  $('#mic').classList.remove('on'); $('#mic').textContent = '◉ VOICE';
  log('voice','VOICE','conversation closed');
  setState('', 'STANDBY', 'awaiting uplink');
}

// ── one listening turn ──
function beginTurn(){
  if (!convo || suppress || !micStream) return;
  chunks = []; spoke = false; peak = 0;
  floorSum = 0; floorN = 0; calibrating = true; threshold = 0.02;
  recorder = new MediaRecorder(micStream, pickMime());
  recorder.ondataavailable = e => { if (e.data?.size) chunks.push(e.data); };
  recorder.onstop = ship;
  recorder.start(250);                          // timeslice → data flows reliably
  turnStart = loudAt = performance.now();
  setState('listening','LISTENING','listening…');
  if (!vad) vad = setInterval(vtick, 40);
}

function meter(rms){
  // live input bar in the reactor subtitle, so you can SEE it hearing you
  const n = Math.max(0, Math.min(14, Math.round(rms * 90)));
  $('#strun').textContent = 'IN ' + '▮'.repeat(n) + '▯'.repeat(14 - n);
}

function endTurn(reason){
  log('voice','VOICE',`${reason} · peak ${peak.toFixed(3)} · thr ${threshold.toFixed(3)}`);
  try { recorder.stop(); } catch(_){}           // → ship()
}

function pickMime(){
  for (const m of ['audio/webm;codecs=opus','audio/webm','audio/mp4'])
    if (window.MediaRecorder?.isTypeSupported?.(m)) return {mimeType:m};
  return undefined;
}

function vtick(){
  if (!analyser || suppress || !recorder || recorder.state!=='recording') return;
  analyser.getByteTimeDomainData(vdata);
  let s=0; for (let i=0;i<vdata.length;i++){ const v=(vdata[i]-128)/128; s+=v*v; }
  const rms = Math.sqrt(s/vdata.length);
  const t = performance.now();
  peak = Math.max(peak, rms);
  meter(rms);

  // first 300ms: measure the room's noise floor, set a threshold just above it
  if (calibrating){
    floorSum += rms; floorN++;
    if (t - turnStart > 300){
      const floor = floorSum / Math.max(1, floorN);
      threshold = Math.max(0.012, floor * 2.2 + 0.006);
      calibrating = false;
    }
    return;
  }

  if (rms > threshold){
    loudAt = t;
    if (!spoke){ spoke = true; log('voice','VOICE','speech detected'); }
  } else if (spoke && t - loudAt > SILENCE){
    return endTurn('silence');
  }

  // failsafes so it can never hang: cap the length, re-listen if no speech at all
  if (t - turnStart > MAX_TURN_MS) return endTurn('max-length');
  if (!spoke && t - turnStart > NO_SPEECH_MS) return endTurn('no-speech');
}

async function ship(){
  const cancel = recorder?._cancel;
  const blob = new Blob(chunks, {type: recorder?.mimeType || 'audio/webm'}); chunks=[];
  if (cancel){ return; }
  // no speech heard, too short, or too small — just listen again, don't ship ambient noise
  if (!spoke || blob.size < 1400 || performance.now()-turnStart < MIN_TURN_MS){
    if (convo && !suppress) beginTurn();
    return;
  }
  setState('running','TRANSCRIBING','…');
  log('voice','VOICE',`uploading ${Math.round(blob.size/1024)}KB to server STT`);
  const t0 = performance.now();
  try {
    const r = await fetch('/api/listen', {method:'POST',
      headers:{'content-type':blob.type||'audio/webm'}, body:blob}).then(r=>r.json());
    const text = (r.text||'').trim();
    const ms = Math.round(performance.now() - t0);
    if (!text){
      log('voice','VOICE','nothing transcribed');
      if (convo) beginTurn(); else setState('','STANDBY','nothing heard');
      return;
    }
    log('voice','VOICE',`transcribed by ElevenLabs in ${ms}ms: "${text}"`);
    const armed = $('#input').dataset.cmd || '';
    const full = (armed ? armed + ' ' : '') + text;
    $('#input').value = ''; disarm();
    log('send','SEND',`auto-sent voice: ${full}`);
    await transmit(full);                       // runs, then speaks; both with mic deaf
    // turn's done — hand the floor back and listen again
    suppress = false;
    if (convo) beginTurn();
  } catch(e){
    log('error','VOICE','transcription failed');
    suppress = false;
    if (convo) beginTurn(); else setState('error','FAULT','transcription failed');
  }
}

// ══════════ command matrix ══════════
function disarm(){
  document.querySelectorAll('.cmd').forEach(c=>c.classList.remove('armed'));
  $('#input').dataset.cmd = '';
}
$('#cmds').addEventListener('click', e => {
  const b = e.target.closest('.cmd'); if (!b) return;
  const cmd = b.dataset.cmd;
  if ($('#input').dataset.cmd === cmd){ disarm(); $('#tip').textContent = defaultTip; return; }
  disarm(); b.classList.add('armed'); $('#input').dataset.cmd = cmd;
  $('#input').focus();
  $('#tip').textContent = TIPS[cmd] || `Armed ${cmd} — now type or speak.`;
  log('command', 'COMMAND', `armed ${cmd}`);
});
const TIPS = {
  '/new':'Fresh thread — clears the conversation and starts Jarvis clean.',
  '/goal':'Say your standing objective, e.g. “grow the newsletter to 5k”.',
  '/profile':'Tell Jarvis about you — it saves it for every future run.',
  '/background':'Say a mission, e.g. “research competitors and save a report”.',
  '/personality':'Set the persona, e.g. “dry, brief, British butler”.',
  '/kanban':'Say a task to queue it, or transmit alone to hear the queue.',
  '/tools':'Transmit alone to hear which tools and connectors are live.',
  '/commands':'Transmit alone for the full command list.'
};
let defaultTip = '';

// ── connected-tools chips (filled from the first run's STATUS event) ──
function setChips(list){
  const box = $('#chips');
  if (!list || !list.length){
    box.innerHTML = '<span class="chip muted">no connectors loaded</span>';
    return;
  }
  box.innerHTML = list.map(n =>
    `<span class="chip on">${esc(String(n).replace(/^claude\.ai /,'').replace(/^plugin:/,''))}</span>`
  ).join('');
}

// ══════════ controls ══════════
function sendFromInput(){
  const armed = $('#input').dataset.cmd || '';
  const raw = $('#input').value.trim();
  if (!raw && !armed) return;
  const full = (armed && !raw.startsWith('/')) ? `${armed} ${raw}` : (raw || armed);
  $('#input').value = ''; disarm(); $('#tip').textContent = defaultTip;
  log('send','SEND',`transmit: ${full}`);
  transmit(full);
}
$('#run').onclick = sendFromInput;
// typing box inside the JARVIS Response panel: Return sends (a /command button
// that's armed on the left still applies)
$('#askForm').addEventListener('submit', e => {
  e.preventDefault();
  const v = $('#ask').value.trim();
  if (!v && !$('#input').dataset.cmd) return;
  $('#input').value = v; $('#ask').value = '';
  sendFromInput();
  $('#ask').focus();
});
window.addEventListener('load', () => { try { $('#ask').focus(); } catch (_){} });
// VOICE uses the background "Hey JARVIS" listener (it has the real microphone and
// speaks the answer); the answer shows here through the live feed. Only if that
// listener isn't running does it fall back to this page's own microphone.
$('#mic').onclick = async () => {
  try {
    const j = await fetch('/api/wake', {method:'POST'}).then(r => r.json());
    if (j.ok){ log('voice', 'VOICE', 'listening — speak now'); return; }
  } catch (_){}
  micToggle();
};
$('#mute').onclick = () => {
  muted = !muted; const b=$('#mute');
  b.textContent = muted?'🔇':'🔊'; b.classList.toggle('on',!muted); b.classList.toggle('off',muted);
  if (muted && player) player.pause();
};
$('#input').addEventListener('keydown', e => {
  if (e.key==='Enter' && (e.metaKey||e.ctrlKey)){ e.preventDefault(); sendFromInput(); }
});
$('#cmds2').onclick = () => transmit('/commands');
$('#clear').onclick = () => {
  $('#input').value = ''; disarm(); $('#tip').textContent = defaultTip;
  $('#response').innerHTML = '<span class="ph">Standing by. Transmit an instruction.</span>';
  setTag('', 'IDLE'); setState('', 'STANDBY', 'awaiting uplink');
};

// ── mission controls ──
document.querySelector('.mctl').addEventListener('click', e => {
  const b = e.target.closest('.mbtn'); if (!b) return;
  switch (b.dataset.mc){
    case 'status':
      log('status','STATUS', `voice=${RT.tts?'elevenlabs':'off'} · `
        + `session=${$('#sy-session').textContent} · uplink=${now()}`);
      break;
    case 'tools':  transmit('/tools'); break;
    case 'queue':  transmit('/kanban'); break;
    case 'wipe':   $('#log').innerHTML = ''; log('note','LOG','cleared'); break;
  }
});

// ══════════ system panel ══════════
function sys(id, val, cls){
  const el = $('#sy-' + id); if (!el) return;
  el.textContent = val; if (cls != null) el.className = cls;
}
// live clock in the system panel
setInterval(() => sys('clock', now()), 1000);

// poll for finished /background missions and report them when they land
setInterval(async () => {
  try {
    const j = await fetch('/api/jobs').then(r => r.json());
    for (const d of (j.done || [])){
      log('complete','MISSION', `${d.mission} → ${(d.result||'').slice(0,200)}`);
      if (!running) speak(`Mission complete. ${(d.result||'').slice(0,300)}`);
    }
  } catch(_){}
}, 4000);

// ══════════ boot ══════════
(async () => {
  defaultTip = $('#tip').textContent;
  try {
    const s = await fetch('/api/status').then(r=>r.json());
    RT.tts = s.tts==='elevenlabs'; RT.stt = s.stt==='elevenlabs';

    const port = location.port || '80';
    sys('gw', `online · :${port}`, 'ok');
    sys('brain', 'JARVIS Core', 'ok');
    sys('voice', RT.tts ? 'ElevenLabs' : 'off', RT.tts ? '' : 'warn');
    sys('profile', 'default');
    sys('session', s.session ? s.session.slice(0,8) : 'none');
    sys('clock', now());

    // header pills
    $('#p-gw').textContent = `Gateway: online ${location.origin}`;
    $('#p-voice').textContent = RT.tts
      ? 'Voice: ElevenLabs STT / ElevenLabs TTS' : 'Voice: unavailable';
    if (!RT.tts) $('#p-voice').previousElementSibling?.classList.replace('am','bad');

    log('status', 'BOOT',
        `gateway online · core ready · permission=${s.permission}`);
    log('voice', 'VOICE', `channel ready — ElevenLabs STT / ElevenLabs TTS`);
    setState('', 'STANDBY', 'awaiting uplink');
  } catch(e){
    sys('gw','offline','warn');
    $('#p-gw').textContent = 'Gateway: offline';
    $('#p-gw-dot').classList.replace('ok','bad');
    log('error','STATUS','server unreachable');
  }
})();

/* ══════════ live feed: "Hey JARVIS" and pop-up turns ══════════
   The background listener and the pop-up talk to the server directly. The
   server mirrors their turns into /api/feed; this shows them here as they
   happen, so you never need the VOICE button while the dashboard is open.
   The listener does the speaking — this page only displays. */
let feedSeq = -1, mirroring = false;
const SRC_LABEL = {voice: 'HEY JARVIS', popup: 'POP-UP'};

function mirror(ev){
  if (running) return;                       // this page's own run has the panel
  if (ev.t === 'standby'){ mirroring = false; showStandby(false); return; }   // listener says it aloud
  if (ev.t === 'mirror_start'){
    mirroring = true; answer = ''; firstDelta = false;
    setState('running', 'RUNNING', (SRC_LABEL[ev.src] || ev.src).toLowerCase());
    setTag('run', 'RUNNING');
    $('#response').innerHTML = '<span class="cur"></span>';
    log('send', SRC_LABEL[ev.src] || 'UPLINK', `"${ev.message}"`);
    return;
  }
  if (!mirroring) return;
  if (ev.t === 'complete'){
    mirroring = false;
    if (STANDBY_ANS.test(answer.trim())){
      showStandby(false); renderAnswer(true); return;
    }
    setState('done', 'COMPLETE', ev.ms != null ? `${ev.ms}ms` : '');
    setTag('done', 'COMPLETE');
    log('complete', 'COMPLETE', ev.ms != null ? `run completed in ${ev.ms}ms` : 'run completed');
    renderAnswer(true);
    return;
  }
  handle(ev);
  if (ev.t === 'error') mirroring = false;
}

(async function feedLoop(){
  while (true){
    try {
      const j = await fetch('/api/feed?since=' + feedSeq).then(r => r.json());
      if (feedSeq >= 0) j.events.forEach(mirror);
      feedSeq = j.seq;
    } catch (_){
      await new Promise(r => setTimeout(r, 2000));
    }
  }
})();


/* ══════════ TODAY panel: assignments · inbox · games ══════════ */
function dayLabel(iso){
  const d = new Date(iso + (iso.length === 10 ? 'T12:00:00' : ''));
  const t = new Date(); t.setHours(12,0,0,0);
  const diff = Math.round((d - t) / 864e5);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  return d.toLocaleDateString(undefined, {weekday:'short', month:'short', day:'numeric'});
}

function renderToday(b){
  $('#today-date').textContent = new Date().toLocaleDateString(undefined, {weekday:'long', month:'short', day:'numeric'});

  // assignments: due tomorrow + the day after, with an arrow to Blackbaud in Chrome
  const a = Array.isArray(b.assignments) ? b.assignments : null;
  const noBB = !a && /BLACKBAUD_ICAL|Expecting value/.test(JSON.stringify(b.assignments || ''));
  $('#t-assign').innerHTML = noBB ? `<div class="tmuted">Not set up — add your Blackbaud feed (see README).</div>`
    : !a ? `<div class="tmuted">Couldn't read Blackbaud right now.</div>`
    : `<div class="tcounts">` + a.map(d =>
        `<div class="tcount" title="${esc(d.items.map(i => i.cls + ': ' + i.title).join('\n') || 'Nothing due')}">`
        + `<div class="n ${d.count ? '' : 'zero'}">${d.count}</div>`
        + `<div class="d">${esc(d.label)}</div></div>`).join('')
      + `<button class="tgo" id="t-bb" title="Open Blackbaud in Chrome">→</button></div>`
      + a.filter(d => d.count).map(d => `<div class="tnames"><span>${esc(d.label)}:</span> `
        + d.items.map(i => esc(i.title)).join(' · ') + `</div>`).join('');
  const bb = $('#t-bb');
  if (bb) bb.onclick = () => fetch('/api/open-blackbaud', {method:'POST'});

  // next calendar event (just one)
  const ev = (b.event || {}).next;
  $('#t-event').innerHTML = (b.event && b.event.at == null && !b.event.error)
    ? `<div class="tmuted">Checking your calendar…</div>`
    : (!ev ? `<div class="tmuted">${b.event && b.event.error ? "Couldn't check your calendar." : 'Nothing coming up.'}</div>`
      : (() => {
          const allDay = /^\d{4}-\d{2}-\d{2}$/.test(ev.start);
          const st = new Date(allDay ? ev.start + 'T12:00:00' : ev.start);
          const en = ev.end ? new Date(ev.end) : null;
          const time = allDay ? 'All day'
            : st.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})
              + (en && !isNaN(en) ? ' – ' + en.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'}) : '');
          return `<div class="ewhen">${esc(dayLabel(st.toLocaleDateString('en-CA')))} · ${esc(time)}</div>`
               + `<div class="ewhat">${esc(ev.title || '(no title)')}</div>`
               + (ev.location ? `<div class="ewhere">${esc(ev.location)}</div>` : '');
        })());

  const m = b.mail || {};
  $('#t-mail').innerHTML = (m.count == null)
    ? `<div class="tmuted">${m.error ? "Couldn't check Gmail." : 'Checking Gmail…'}</div>`
    : `<div class="big">${m.count}</div><div class="sub">unread in your inbox`
      + (m.all != null && m.all > m.count ? `<br>${m.all} unread across all mail` : '') + `</div>`;

  const g = Array.isArray(b.games) ? b.games : [];
  $('#t-games').innerHTML = !Array.isArray(b.games) ? `<div class="tmuted">Schedules unavailable.</div>` :
    !g.length ? `<div class="tmuted">Add your teams with JARVIS_TEAMS in .env.</div>` :
    g.map(t => {
      const n = t.next, l = t.last;
      let nx = 'No upcoming game found';
      if (n) nx = n.state === 'in' ? `LIVE ${esc(n.vs)} · ${esc(n.us_score||'0')}–${esc(n.them_score||'0')} · ${esc(n.detail)}`
             : `${n.today ? 'TODAY' : esc(n.local.split(' · ')[0])} · ${esc(n.local.split(' · ')[1]||'')} ${esc(n.vs)}${n.tv ? ' · ' + esc(n.tv) : ''}`;
      const ls = l ? `Last: <span class="${l.won ? 'w' : 'l'}">${l.won ? 'W' : 'L'} ${esc(l.us_score)}–${esc(l.them_score)}</span> ${esc(l.vs)}` : '';
      return `<div class="tgame">${t.logo ? `<img src="${esc(t.logo)}" alt="">` : '<div></div>'}<div>`
           + `<div class="nm">${esc(t.short || t.name)}<small>${esc(t.record)}</small></div>`
           + `<div class="nx ${n && (n.today || n.state === 'in') ? 'live' : ''}">${nx}</div>`
           + `<div class="ls">${ls}</div></div></div>`;
    }).join('');

  $('#t-updated').textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
}

async function loadToday(){
  try { renderToday(await fetch('/api/brief').then(r => r.json())); }
  catch (_) { $('#t-updated').textContent = "Couldn't refresh — is JARVIS running?"; }
}
loadToday();
setInterval(loadToday, 60 * 1000);       // the server caches; this just picks up changes


/* ══════════ local clock + world population counter ══════════ */
const SF_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;   // this Mac's time zone
// UN World Population Prospects 2024, medium projection (1 July of each year).
// Between/after these points the count moves linearly at the projected rate
// (~2.2 people per second) — the same way "live" population clocks work.
const POP_POINTS = [[Date.UTC(2025, 6, 1), 8231613070], [Date.UTC(2026, 6, 1), 8300678395],
                    [Date.UTC(2027, 6, 1), 8368376855]];
function worldPop(t){
  let [a, b] = [POP_POINTS[0], POP_POINTS[1]];
  for (let i = 1; i < POP_POINTS.length; i++){ a = POP_POINTS[i-1]; b = POP_POINTS[i]; if (t <= b[0]) break; }
  return Math.round(a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0]));
}
const fmtTime = new Intl.DateTimeFormat('en-US', {timeZone: SF_TZ, hour:'numeric', minute:'2-digit', second:'2-digit', hour12:true});
const fmtDate = new Intl.DateTimeFormat('en-US', {timeZone: SF_TZ, weekday:'long', month:'long', day:'numeric'});
const fmtZone = new Intl.DateTimeFormat('en-US', {timeZone: SF_TZ, timeZoneName:'short'});
const zoneName = t => (fmtZone.formatToParts(t).find(p => p.type === 'timeZoneName') || {}).value || '';
function tickClocks(){
  const now = new Date();
  const parts = fmtTime.formatToParts(now);
  const get = k => (parts.find(p => p.type === k) || {}).value || '';
  const el = $('#c-time');
  if (el) el.innerHTML = `${get('hour')}:${get('minute')}:${get('second')}<small>${get('dayPeriod')}</small>`;
  const d = $('#c-date'); if (d) d.textContent = `${fmtDate.format(now)} · ${zoneName(now)}`;
  const p = $('#pop'); if (p) p.textContent = worldPop(now.getTime()).toLocaleString('en-US');
}
tickClocks();
setTimeout(() => { tickClocks(); setInterval(tickClocks, 1000); }, 1000 - (Date.now() % 1000));  // tick on the second
setInterval(() => { const p = $('#pop'); if (p) p.textContent = worldPop(Date.now()).toLocaleString('en-US'); }, 200);

// ── tell the Mac when this page is in front ──
// While it is, the pop-up bar stays hidden (this page shows the conversation) and
// voice goes hands-free: a 2-second pause ends your turn, no ⌘ tap needed.
function reportFocus(){
  if (window.__JARVIS_APP) return;           // inside JARVIS.app: the app window reports focus itself
  const on = document.visibilityState === 'visible' && document.hasFocus();
  fetch('/api/focus', {method:'POST', headers:{'Content-Type':'application/json'},
                       body: JSON.stringify({on})}).catch(() => {});
}
window.addEventListener('focus', reportFocus);
window.addEventListener('blur', reportFocus);
document.addEventListener('visibilitychange', reportFocus);
window.addEventListener('pagehide', () =>
  navigator.sendBeacon?.('/api/focus', new Blob([JSON.stringify({on:false})], {type:'application/json'})));
reportFocus();
setInterval(reportFocus, 10000);    // heartbeat: a stale flag (page closed) counts as "not in front"

/* ══════════ Go on standby button ══════════
   One click: the listener stops talking/listening, the circle shows STANDBY. */
$('#standby').onclick = async () => {
  try { await fetch('/api/standby', {method:'POST', headers:{'Content-Type':'application/json'},
                                      body: JSON.stringify({from:'button'})}); } catch {}
  showStandby(false);
};
