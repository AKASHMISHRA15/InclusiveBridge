window.VoiceChat = (function () {
    const MAX_RECORD_MS = 60000;

    function normalizeType(m) {
        if (!m) return 'message';
        return m.msg_type || m.type || 'message';
    }

    function isVoiceUrl(text) {
        return typeof text === 'string' && text.includes('/chat/voice/');
    }

    function isVoiceMessage(m) {
        if (!m) return false;
        return normalizeType(m) === 'voice' || isVoiceUrl(m.message);
    }

    function voiceSrc(m) {
        if (!m || !m.message) return '';
        const msg = String(m.message);
        if (msg.startsWith('/')) return msg;
        if (isVoiceUrl(msg)) return msg.startsWith('http') ? msg : msg;
        return `/chat/voice/${msg}`;
    }

    function voicePlayerHtml(m) {
        const src = voiceSrc(m);
        if (!src) return '<span class="voice-msg-label">🎙️ Voice message (unavailable)</span>';
        return `
            <div class="voice-msg-wrap">
                <span class="voice-msg-label">🎙️ Voice message</span>
                <audio controls preload="auto" class="voice-msg-player" src="${src}" crossorigin="anonymous"></audio>
            </div>`;
    }

    function renderBody(m) {
        if (isVoiceMessage(m)) return voicePlayerHtml(m);
        return escapeHtml(m.message || '');
    }

    function renderLiveBody(m) {
        if (isVoiceMessage(m)) return voicePlayerHtml(m);
        return escapeHtml(m.message || '');
    }

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function messagesFingerprint(messages) {
        if (!Array.isArray(messages)) return '';
        return messages.map((m) => [
            m.sender || '',
            m.message || '',
            normalizeType(m),
            m.timestamp || '',
        ].join('|')).join('\n');
    }

    function pickMimeType() {
        const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
        for (const t of types) {
            if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t;
        }
        return '';
    }

    function initVoiceRecorder(buttonEl, sender, onSent) {
        if (!buttonEl) return;
        if (!window.MediaRecorder) {
            buttonEl.disabled = true;
            buttonEl.title = 'Voice messages not supported in this browser';
            return;
        }

        let mediaRecorder = null;
        let mediaStream = null;
        let chunks = [];
        let recording = false;
        let maxTimer = null;

        buttonEl.addEventListener('click', async () => {
            if (recording) {
                stopRecording(true);
                return;
            }
            await startRecording();
        });

        async function startRecording() {
            try {
                mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                chunks = [];
                const mimeType = pickMimeType();
                const options = mimeType ? { mimeType } : undefined;
                mediaRecorder = new MediaRecorder(mediaStream, options);

                mediaRecorder.ondataavailable = (e) => {
                    if (e.data && e.data.size > 0) chunks.push(e.data);
                };

                mediaRecorder.onstop = async () => {
                    cleanupStream();
                    if (!chunks.length) return;
                    const type = mediaRecorder.mimeType || 'audio/webm';
                    const blob = new Blob(chunks, { type });
                    chunks = [];
                    try {
                        await uploadVoice(blob, sender, type);
                        if (typeof onSent === 'function') onSent();
                    } catch (err) {
                        console.error('Voice upload failed:', err);
                        alert('Could not send voice message. Please try again.');
                    }
                };

                mediaRecorder.start(250);
                recording = true;
                buttonEl.classList.add('recording');
                buttonEl.title = 'Recording… tap to stop and send';
                maxTimer = setTimeout(() => stopRecording(true), MAX_RECORD_MS);
            } catch (err) {
                console.error('Voice record error:', err);
                alert('Microphone access is required to record voice messages.');
                cleanupStream();
                resetButton();
            }
        }

        function stopRecording(send) {
            if (!recording) return;
            recording = false;
            clearTimeout(maxTimer);
            resetButton();
            if (send && mediaRecorder && mediaRecorder.state !== 'inactive') {
                mediaRecorder.stop();
            } else {
                cleanupStream();
                chunks = [];
            }
        }

        function cleanupStream() {
            if (mediaStream) {
                mediaStream.getTracks().forEach((t) => { try { t.stop(); } catch {} });
                mediaStream = null;
            }
        }

        function resetButton() {
            buttonEl.classList.remove('recording');
            buttonEl.title = 'Record voice message';
        }

        async function uploadVoice(blob, voiceSender, mimeType) {
            const fd = new FormData();
            const ext = mimeType.includes('mp4') ? 'm4a' : 'webm';
            fd.append('file', blob, `voice.${ext}`);
            fd.append('sender', voiceSender);
            const res = await fetch('/chat/send-voice', { method: 'POST', body: fd });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.message || 'Upload failed');
            return data;
        }
    }

    return {
        normalizeType,
        isVoiceMessage,
        isVoiceUrl,
        renderBody,
        renderLiveBody,
        messagesFingerprint,
        initVoiceRecorder,
    };
})();
