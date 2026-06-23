let ttsEnabled = false;
let lastMessageCount = 0;
let isViewingHistory = false;
let systemIsRunning = true;
let _chatFingerprint = '';
let keyboard = null;        // Simple-Keyboard instance (mobile only)
let _notifEnabled = false;  // tracks notification toggle state

/* ── VOICE MESSAGE HELPERS ── */

function isVoiceMsg(m) {
    if (!m) return false;
    const t = (m.msg_type || m.type || '').toLowerCase();
    const msg = String(m.message || '');
    return t === 'voice' || msg.indexOf('/chat/voice/') !== -1;
}

function voiceAudioSrc(m) {
    return String(m.message || '');
}

function voicePlayerHtml(m) {
    const src = voiceAudioSrc(m);
    if (!src || src.indexOf('/chat/voice/') === -1) {
        return '<span class="voice-msg-label">🎙️ Voice message (unavailable)</span>';
    }
    return `<div class="voice-msg-wrap">
        <span class="voice-msg-label">🎙️ Voice message</span>
        <audio controls preload="metadata" class="voice-msg-player" src="${src}"></audio>
    </div>`;
}

function renderMsgBody(m) {
    if (isVoiceMsg(m)) return voicePlayerHtml(m);
    return String(m.message || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function chatFingerprint(messages) {
    return (messages || []).map((m) =>
        [m.sender, m.message, m.msg_type || m.type, m.timestamp].join('|')
    ).join('\n');
}

function showIncomingNotification(title, body) {
    if (!document.hidden || !('Notification' in window) || Notification.permission !== 'granted') return;
    try {
        new Notification(title, {
            body,
            tag: 'inclusivebridge-message',
            renotify: true,
            vibrate: [200, 100, 200]
        });
    } catch (err) {
        console.warn('Browser notification failed:', err);
    }
}

function showNotificationTest() {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
        new Notification('InclusiveBridge alerts enabled', {
            body: 'This is a test notification from this browser.',
            tag: 'inclusivebridge-test',
            renotify: true
        });
    } catch (err) {
        console.warn('Test notification failed:', err);
    }
}

function initVoiceRecorder(buttonEl, sender, onSent) {
    if (!buttonEl || !window.MediaRecorder) {
        if (buttonEl) { buttonEl.disabled = true; buttonEl.title = 'Voice not supported'; }
        return;
    }
    let mediaRecorder = null, mediaStream = null, chunks = [], recording = false, maxTimer = null;

    buttonEl.addEventListener('click', async () => {
        if (recording) { stopRec(true); return; }
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            chunks = [];
            const mime = ['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(t => MediaRecorder.isTypeSupported(t)) || '';
            mediaRecorder = mime ? new MediaRecorder(mediaStream, { mimeType: mime }) : new MediaRecorder(mediaStream);
            mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
            mediaRecorder.onstop = async () => {
                mediaStream.getTracks().forEach(t => t.stop());
                if (!chunks.length) return;
                const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
                chunks = [];
                const fd = new FormData();
                fd.append('file', blob, blob.type.includes('mp4') ? 'voice.m4a' : 'voice.webm');
                fd.append('sender', sender);
                const res = await fetch('/chat/send-voice', { method: 'POST', body: fd });
                if (!res.ok) { alert('Could not send voice message.'); return; }
                if (onSent) onSent();
            };
            mediaRecorder.start(250);
            recording = true;
            buttonEl.classList.add('recording');
            buttonEl.title = 'Tap to stop and send';
            maxTimer = setTimeout(() => stopRec(true), 60000);
        } catch (e) {
            alert("Microphone permission was blocked.\n\nTo unblock it:\n1. Click the 🔒 lock icon in your browser address bar.\n2. Turn on the Microphone permission.\n3. Try again.");
        }
    });

    function stopRec(send) {
        if (!recording) return;
        recording = false;
        clearTimeout(maxTimer);
        buttonEl.classList.remove('recording');
        buttonEl.title = 'Record voice message';
        if (send && mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
        else if (mediaStream) mediaStream.getTracks().forEach(t => t.stop());
    }
}

async function startSystem() {
    await fetch('/start-session', { method: 'POST' });
}

async function updateDashboard() {
    try {
        const loadingOverlay = document.getElementById('model-loading');
        if (loadingOverlay && loadingOverlay.style.display !== 'none') return;

        const response = await fetch('/status');
        const data = await response.json();
        const connStatus = document.getElementById('connection-status');
        if (connStatus) {
            if (!data.session_active) {
                connStatus.innerText = "🔴 Session Ended";
                connStatus.style.color = "#ef4444";
                systemIsRunning = false;
            } else if (!data.running) {
                connStatus.innerText = "🟡 Monitoring Paused";
                connStatus.style.color = "#facc15";
                systemIsRunning = false;
            } else {
                connStatus.innerText = "🟢 System Live";
                connStatus.style.color = "#22c55e";
                systemIsRunning = true;
            }
        }
    } catch (e) {
        const connStatus = document.getElementById('connection-status');
        if (connStatus) {
            connStatus.innerText = "⚠ Server Offline";
            connStatus.style.color = "#ef4444";
        }
    }
}

function addLog(message) {
    const container = document.getElementById('log-container');
    if (!container) return;
    const entry = document.createElement('p');
    entry.className = 'log-entry';
    entry.innerHTML = `<span style="color: #64748b">[${new Date().toLocaleTimeString()}]</span> ${message}`;
    container.prepend(entry);
    if (container.children.length > 15) container.removeChild(container.lastChild);
}

/* ── CHAT ── */

async function sendMessage(text) {
    if (!text || !text.trim()) return;
    
    // Optimistic UI update
    const t = text.trim();
    const msgObj = { sender: "Patient", message: t, timestamp: new Date().toLocaleTimeString('en-US', {hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit"}) };
    const liveDisplay = document.getElementById('live-chat-display');
    if (liveDisplay && typeof renderLiveMessage !== 'undefined') {
        liveDisplay.insertAdjacentHTML('beforeend', renderLiveMessage(msgObj));
        liveDisplay.scrollTop = liveDisplay.scrollHeight;
    }
    const chatBox = document.getElementById('chat-box');
    if (chatBox && typeof renderMessage !== 'undefined') {
        chatBox.insertAdjacentHTML('beforeend', renderMessage(msgObj));
        chatBox.scrollTop = chatBox.scrollHeight;
    }
    
    const input = document.getElementById('chat-input');
    if (input) input.value = "";
    if (typeof keyboard !== 'undefined' && keyboard) keyboard.clearInput();

    // Send async without blocking UI
    fetch('/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sender: "Patient", text: t })
    }).then(() => {
        _chatFingerprint = '';
        updateChat();
    });
}

function renderMessage(m) {
    const senderClass = m.sender.toLowerCase();
    const timeStr = m.timestamp ? `<span class="msg-time">${m.timestamp}</span> ` : '';
    return `<div class="message ${senderClass}">${timeStr}<strong>${m.sender}:</strong> ${renderMsgBody(m)}</div>`;
}

function renderLiveMessage(m) {
    const isPatient = m.sender === 'Patient';
    const isSystem  = m.sender === 'System';
    const isAI      = m.sender === 'AI';
    // System alerts & AI messages originate from the patient's device — show on right (sent) side
    let side = 'received';
    if (isPatient || isSystem || isAI) side = 'sent';
    let senderLabel = m.sender.toUpperCase();
    if (isSystem) senderLabel = '🚨 ALERT';
    if (isAI) senderLabel = '✨ AI';
    if (isVoiceMsg(m)) {
        return `<div class="live-msg ${m.sender.toLowerCase()} ${side}"><small>${senderLabel}</small>${voicePlayerHtml(m)}</div>`;
    }
    return `<div class="live-msg ${m.sender.toLowerCase()} ${side}"><small>${senderLabel}</small><p>${renderMsgBody(m)}</p></div>`;
}

async function updateChat() {
    if (isViewingHistory) return;
    let messages = [];
    try {
        const response = await fetch('/chat');
        messages = await response.json();
    } catch (e) { return; }

    const fp = chatFingerprint(messages);
    if (fp === _chatFingerprint) return;
    _chatFingerprint = fp;

    const liveDisplay = document.getElementById('live-chat-display');
    if (liveDisplay) {
        liveDisplay.innerHTML = messages.slice(-100).map(renderLiveMessage).join('');
        liveDisplay.scrollTop = liveDisplay.scrollHeight;
    }

    const chatBox = document.getElementById('chat-box');
    if (chatBox) {
        chatBox.innerHTML = messages.map(renderMessage).join('');
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    if (messages.length > lastMessageCount) {
        const newest = messages[messages.length - 1];
        if (!isVoiceMsg(newest)) {
            speakOutLoud(`${newest.sender} says: ${newest.message}`);
            if (newest.sender !== 'Patient') {
                showIncomingNotification(`Message from ${newest.sender}`, newest.message);
            }
        }
        lastMessageCount = messages.length;
    }
}

/* ── SPEECH ── */

let recognition = null;
try {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SR) recognition = new SR();
} catch (e) { console.warn("Speech Recognition not available:", e); }

function speakOutLoud(text) {
    if (!ttsEnabled || !text || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    const voices = window.speechSynthesis.getVoices();
    const localVoice = voices.find(v => v.localService === true && v.lang.startsWith('en'));
    if (localVoice) utterance.voice = localVoice;
    window.speechSynthesis.speak(utterance);
}

function fixMobileHeight() {
    document.documentElement.style.setProperty('--app-height', `${window.innerHeight}px`);
}

/* ── NOTIFICATION TOGGLE ── */
window.toggleNotifications = async function() {
    const btn = document.getElementById('enable-notifications-btn');
    if (!('Notification' in window)) {
        showToast('Your browser does not support notifications.', 'warning');
        return;
    }
    if (Notification.permission === 'denied') {
        alert("Notifications permission was blocked.\n\nTo unblock it:\n1. Click the 🔒 lock icon in your browser address bar.\n2. Turn on Notifications.\n3. Try again.");
        return;
    }
    if (!_notifEnabled) {
        const perm = await Notification.requestPermission();
        if (perm === 'granted') {
            const subscribed = await setupPushSubscription();
            if (!subscribed) return;
            _notifEnabled = true;
            if (btn) { btn.textContent = '🔕 Disable Alerts'; btn.style.background = '#16a34a'; }
            showToast('✅ Notifications ENABLED — you will be alerted for new messages.', 'success');
            showNotificationTest();
        } else {
            showToast('❌ Permission denied. Enable notifications in browser settings.', 'warning');
        }
    } else {
        await disablePushSubscription();
        _notifEnabled = false;
        if (btn) { btn.textContent = '🔔 Alerts'; btn.style.background = '#3b82f6'; }
        showToast('🔕 Notifications DISABLED for this session.', 'info');
    }
};
window.requestNotifications = window.toggleNotifications;

function showToast(message, type) {
    let toast = document.getElementById('notif-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'notif-toast';
        document.body.appendChild(toast);
    }
    const colors = { success: '#16a34a', warning: '#d97706', info: '#3b82f6' };
    toast.style.cssText = `
        position:fixed; bottom:90px; left:50%; transform:translateX(-50%);
        background:${colors[type] || '#3b82f6'}; color:#fff;
        padding:12px 24px; border-radius:12px; font-size:0.9rem; font-weight:600;
        z-index:9999; box-shadow:0 4px 20px rgba(0,0,0,0.45);
        max-width:90vw; text-align:center; pointer-events:none; opacity:1;
        transition: opacity 0.4s;
    `;
    toast.textContent = message;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { toast.style.opacity = '0'; }, 3500);
}

async function setupPushSubscription() {
    if (!('serviceWorker' in navigator && 'PushManager' in window)) {
        showToast('Push alerts are not supported in this browser.', 'warning');
        return false;
    }
    try {
        const reg = await navigator.serviceWorker.register('/service-worker.js');
        let sub = await reg.pushManager.getSubscription();
        if (!sub) {
            const res = await fetch('/api/vapidPublicKey');
            if (!res.ok) throw new Error('VAPID key unavailable');
            const rawKey = await res.text();
            const padding = '='.repeat((4 - rawKey.length % 4) % 4);
            const base64 = (rawKey + padding).replace(/-/g, '+').replace(/_/g, '/');
            const binary = window.atob(base64);
            const arr = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) arr[i] = binary.charCodeAt(i);
            sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: arr });
        }
        const saveRes = await fetch('/api/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sub)
        });
        if (!saveRes.ok) throw new Error('Could not save subscription');
        return true;
    } catch (err) {
        console.warn('Push subscription failed:', err);
        showToast('Notifications are allowed, but alert registration failed. Try resetting site permissions and rejoining the session.', 'warning');
        return false;
    }
}

/* ── MOBILE VIRTUAL KEYBOARD ── */
async function disablePushSubscription() {
    try {
        if (!('serviceWorker' in navigator && 'PushManager' in window)) return;
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) await sub.unsubscribe();
    } catch (err) {
        console.warn('Push unsubscribe failed:', err);
    }
}

function isMobileDevice() {
    return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) ||
           (navigator.maxTouchPoints > 1 && window.innerWidth < 1024);
}

function initMobileKeyboard() {
    const chatInput   = document.getElementById('chat-input');
    const kbContainer = document.getElementById('simple-keyboard');
    if (!chatInput || !kbContainer) return;
    if (typeof window.SimpleKeyboard === 'undefined') {
        // Retry in 500ms if CDN not loaded yet
        setTimeout(initMobileKeyboard, 500);
        return;
    }

    keyboard = new window.SimpleKeyboard.default({
        onChange: input => { chatInput.value = input; },
        onKeyPress: button => {
            if (button === '{enter}') {
                const txt = chatInput.value.trim();
                if (txt) sendMessage(txt);
                return;
            }
            if (button === '{shift}' || button === '{lock}') {
                const cur = keyboard.options.layoutName;
                keyboard.setOptions({ layoutName: cur === 'default' ? 'shift' : 'default' });
            }
        },
        theme: 'hg-theme-default myTheme',
        layout: {
            default: [
                '1 2 3 4 5 6 7 8 9 0 {bksp}',
                'q w e r t y u i o p',
                'a s d f g h j k l',
                '{shift} z x c v b n m , . {shift}',
                '{space} {enter}'
            ],
            shift: [
                '! @ # $ % ^ & * ( ) {bksp}',
                'Q W E R T Y U I O P',
                'A S D F G H J K L',
                '{shift} Z X C V B N M < > {shift}',
                '{space} {enter}'
            ]
        },
        display: {
            '{bksp}':   '⌫',
            '{enter}':  'Send ↵',
            '{shift}':  '⇧ Shift',
            '{space}':  'Space'
        }
    });

    kbContainer.style.display = 'block';
}

/* ── INIT ── */
document.addEventListener('DOMContentLoaded', () => {

    fixMobileHeight();
    window.addEventListener('resize', fixMobileHeight);

    // Restore notification button state if already granted
    if ('Notification' in window && Notification.permission === 'granted') {
        _notifEnabled = true;
        const btn = document.getElementById('enable-notifications-btn');
        if (btn) { btn.textContent = '🔕 Disable Alerts'; btn.style.background = '#16a34a'; }
        setupPushSubscription();
    }

    // Mobile keyboard
    if (isMobileDevice()) {
        setTimeout(initMobileKeyboard, 200);
    }

    const micBtn    = document.getElementById('mic-btn');
    const chatInput = document.getElementById('chat-input');
    const sendBtn   = document.getElementById('send-btn');

    function getChatText() { return chatInput ? chatInput.value.trim() : ''; }
    function clearChatText() {
        if (chatInput) chatInput.value = '';
        if (keyboard) keyboard.clearInput();
    }

    const ttsBtn = document.getElementById('tts-btn');
    if (ttsBtn) {
        ttsBtn.onclick = () => {
            ttsEnabled = !ttsEnabled;
            ttsBtn.textContent = ttsEnabled ? '🔊' : '🔇';
            ttsBtn.classList.toggle('tts-on', ttsEnabled);
            ttsBtn.title = ttsEnabled ? 'TTS On — click to mute' : 'TTS Off — click to enable';
        };
    }

    if (micBtn && recognition) {
        micBtn.onclick = () => { recognition.start(); micBtn.style.backgroundColor = "#ef4444"; };
        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            if (chatInput) chatInput.value = transcript;
            if (keyboard) keyboard.setInput(transcript);
            micBtn.style.backgroundColor = "";
            sendMessage(transcript);
            clearChatText();
        };
        recognition.onerror = (event) => {
            micBtn.style.backgroundColor = "";
            if (event && event.error === 'not-allowed') {
                alert("Microphone permission was blocked.\n\nTo unblock it:\n1. Click the 🔒 lock icon in your browser address bar.\n2. Turn on the Microphone permission.\n3. Try again.");
            }
        };
    } else if (micBtn) {
        micBtn.disabled = true;
        micBtn.title = "Mic not supported in this browser";
    }

    if (sendBtn) {
        sendBtn.onclick = () => { const txt = getChatText(); if (txt) { sendMessage(txt); clearChatText(); } };
    }

    if (chatInput) {
        chatInput.onkeypress = (e) => {
            if (e.key === 'Enter') { const txt = getChatText(); if (txt) { sendMessage(txt); clearChatText(); } }
        };
    }

    initVoiceRecorder(
        document.getElementById('voice-msg-btn'),
        'Patient',
        () => { _chatFingerprint = ''; updateChat(); }
    );

    window.dashboardInterval = setInterval(updateDashboard, 3000);
    window.chatInterval      = setInterval(updateChat, 2000);
    updateChat();
});
