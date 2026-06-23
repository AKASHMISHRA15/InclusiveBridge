

(function () {
    'use strict';

    /* ── State ───────────────────────────────────────────────────────────── */
    let _isOpen       = false;
    let _history      = [];   // [{role:'user'|'ai', text, ts}]
    let _pendingImage = null; // {b64, mime}
    let _chatGetter   = null; // injected by host page: () => messages[]
    let _historyLoaded = false;
    let _lastSessionId = null;
    let _role         = 'Patient'; // 'Patient' or 'Caregiver' — set by host page

    function _storageKey() {
        // Separate history per role so patient & caregiver don't share AI conversations
        return 'ai_chat_' + _role + '_' + _lastSessionId;
    }

    function _saveHistory() {
        if (!_lastSessionId) return;
        localStorage.setItem(_storageKey(), JSON.stringify(_history));
    }

    function _pollSession() {
        setInterval(() => {
            const current = window.currentSessionId || null;
            if (current && current !== _lastSessionId) {
                _lastSessionId = current;
                _historyLoaded = true;

                // Key is role-specific: patient and caregiver have separate AI histories
                const data = localStorage.getItem(_storageKey());
                if (data) {
                    try {
                        const parsed = JSON.parse(data);
                        if (parsed && parsed.length > 0) {
                            _history = parsed;
                            if (_log) _log.innerHTML = '';
                            parsed.forEach(msg => {
                                const div = document.createElement('div');
                                div.className = `ai-msg ai-msg-${msg.role}`;
                                div.innerHTML = `<span class="ai-msg-bubble">${_escHtml(msg.text)}</span>`;
                                if (_log) {
                                    _log.appendChild(div);
                                    _log.scrollTop = _log.scrollHeight;
                                }
                            });
                            return;
                        }
                    } catch(e){}
                }

                // New session or empty history: clear and show greeting
                _history = [];
                if (_log) _log.innerHTML = '';
                _appendMsg('ai', '👋 Hi! I\'m your InclusiveBridge AI assistant. I can summarise alerts, answer questions, translate messages, or set reminders. How can I help?');
            }
        }, 500);
    }


    /* ── Public API injected by host ─────────────────────────────────────── */
    window.AIAssistant = {
        init,
        setContextGetter: (fn) => { _chatGetter = fn; },
        // Call this BEFORE init() to separate patient vs caregiver AI history & sender
        setRole: (role) => { _role = role || 'Patient'; },
    };

    /* ── DOM references (set after init) ─────────────────────────────────── */
    let _panel, _log, _input, _langSelect, _imgPreview, _imgInput, _sendBtn;

    
    function init() {
        _injectStyles();
        _buildButton();
        _buildPanel();
        
        _pollSession();
    }

    /* ── Floating trigger button ──────────────────────────────────────────── */
    function _buildButton() {
        if (document.getElementById('ai-sidebar-container')) return; // No button if embedded

        const btn = document.createElement('button');
        btn.id        = 'ai-assist-btn';
        btn.innerHTML = '✨';
        btn.title     = 'AI Assistant';
        btn.onclick   = togglePanel;

        // Insert above the chat input bar inside .chat-container
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) {
            chatContainer.insertBefore(btn, chatContainer.firstChild);
        } else {
            document.body.appendChild(btn);
        }
    }

    /* ── Slide-up panel ───────────────────────────────────────────────────── */
    function _buildPanel() {
        _panel = document.createElement('div');
        _panel.id = 'ai-panel';
        _panel.innerHTML = `
            <div id="ai-panel-header">
                <span>✨ AI Assistant</span>
                <div style="display:flex;gap:8px;align-items:center;">
                    <select id="ai-lang-select" title="Translation language">
                        <option value="">🌐 No translation</option>
                        <option value="Hindi">🇮🇳 Hindi</option>
                        <option value="Bengali">🇧🇩 Bengali</option>
                        <option value="English">🇬🇧 English</option>
                    </select>
                    <button id="ai-close-btn" title="Close">✕</button>
                </div>
            </div>
            <div id="ai-log"></div>
            <div id="ai-img-preview" style="display:none;">
                <span id="ai-img-name"></span>
                <button onclick="AIAssistant._clearImage()" title="Remove image">✕</button>
            </div>
            <div id="ai-input-row">
                <label id="ai-attach-btn" title="Attach image / document">
                    📎
                    <input type="file" id="ai-img-input" accept="image/*,application/pdf" style="display:none">
                </label>
                <input type="text" id="ai-input" placeholder="Ask anything… or say 'remind patient in 10 min'" style="font-size:16px;">
                <button id="ai-mic-btn" title="Speech to text" style="background:none; border:none; font-size:1.2rem; cursor:pointer; padding:0 4px;">🎤</button>
                <button id="ai-send-btn">Send</button>
            </div>
            <div id="ai-quick-btns">
                <button class="ai-quick" onclick="AIAssistant._quick('Summarize the recent alerts in this session in plain English.')">📋 Summarize alerts</button>
                <button class="ai-quick" onclick="AIAssistant._quick('How many alert messages were sent in the last 30 minutes?')">⏱ Alerts / 30 min</button>
                <button class="ai-quick" onclick="AIAssistant._quick('What is the current posture and expression status based on the chat?')">🧍 Status</button>
            </div>`;

        const container = document.getElementById('ai-sidebar-container');
        if (container) {
            container.appendChild(_panel);
            _panel.style.cssText += [
                'position:relative',
                'width:100%',
                'flex:1',
                'height:auto',
                'max-height:none',
                'bottom:auto',
                'right:auto',
                'border:none',
                'border-radius:0',
                'box-shadow:none',
                'min-height:0',
                'transform:none',
                'transition:none',
                'z-index:auto',
                'display:flex',
                'flex-direction:column',
            ].join(';') + ';';
            _panel.classList.add('ai-embedded');
            const closeBtn = document.getElementById('ai-close-btn');
            if (closeBtn) closeBtn.style.display = 'none';
            _isOpen = true;
            // Greeting is now handled by _tryLoadHistory()
        } else {
            document.body.appendChild(_panel);
        }

        _log        = document.getElementById('ai-log');
        _input      = document.getElementById('ai-input');
        _langSelect = document.getElementById('ai-lang-select');
        _imgPreview = document.getElementById('ai-img-preview');
        _imgInput   = document.getElementById('ai-img-input');
        _sendBtn    = document.getElementById('ai-send-btn');
        const micBtn = document.getElementById('ai-mic-btn');

        document.getElementById('ai-close-btn').onclick = togglePanel;
        _sendBtn.onclick = _sendMessage;
        _input.onkeypress = (e) => { if (e.key === 'Enter') _sendMessage(); };
        _imgInput.onchange = _handleImageAttach;

        // Make panel draggable (only if not embedded)
        if (!document.getElementById('ai-sidebar-container')) {
            const header = document.getElementById('ai-panel-header');
            let isDragging = false, startX, startY, initialX, initialY;
            header.onpointerdown = (e) => {
                if (e.target.tagName === 'BUTTON' || e.target.tagName === 'SELECT') return;
                isDragging = true;
                startX = e.clientX; startY = e.clientY;
                initialX = _panel.offsetLeft; initialY = _panel.offsetTop;
                _panel.style.bottom = 'auto'; _panel.style.right = 'auto'; // Disable constraints
                _panel.style.width = _panel.offsetWidth + 'px';
                _panel.style.left = initialX + 'px'; _panel.style.top = initialY + 'px';
                _panel.style.margin = '0';
                header.setPointerCapture(e.pointerId);
            };
            header.onpointermove = (e) => {
                if (!isDragging) return;
                _panel.style.left = (initialX + (e.clientX - startX)) + 'px';
                _panel.style.top = (initialY + (e.clientY - startY)) + 'px';
            };
            header.onpointerup = (e) => {
                isDragging = false;
                header.releasePointerCapture(e.pointerId);
            };
            header.onpointercancel = (e) => {
                isDragging = false;
                header.releasePointerCapture(e.pointerId);
            };
        }

        // Initialize Speech To Text
        try {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (SR) {
                const recognition = new SR();
                let isRecording = false;
                micBtn.onclick = () => {
                    if (isRecording) { recognition.stop(); return; }
                    recognition.start();
                    isRecording = true;
                    micBtn.style.color = '#ef4444';
                    micBtn.innerText = '🔴';
                };
                recognition.onresult = (e) => {
                    _input.value += (window.AIAssistant._pendingImage ? ' ' : '') + e.results[0][0].transcript;
                };
                recognition.onend = () => {
                    isRecording = false;
                    micBtn.style.color = '';
                    micBtn.innerText = '🎤';
                };
            } else {
                micBtn.style.display = 'none';
            }
        } catch(e) { micBtn.style.display = 'none'; }
    }

    /* ── Toggle open/close ────────────────────────────────────────────────── */
    function togglePanel() {
        _isOpen = !_isOpen;
        _panel.classList.toggle('ai-panel-open', _isOpen);
        const btn = document.getElementById('ai-assist-btn');
        if (btn) btn.classList.toggle('ai-btn-active', _isOpen);
        if (_isOpen && _log.innerHTML === '') {
            _appendMsg('ai', '👋 Hi! I\'m your InclusiveBridge AI assistant. I can summarise alerts, answer questions, translate messages, extract text from images, or set reminders. How can I help?');
        }
        if (_isOpen) setTimeout(() => _input && _input.focus(), 200);
    }

    /* ── Image attachment ─────────────────────────────────────────────────── */
    function _handleImageAttach(e) {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => {
            const dataUrl = ev.target.result;
            const b64     = dataUrl.split(',')[1];
            _pendingImage = { b64, mime: file.type || 'image/jpeg', name: file.name };
            _imgPreview.style.display = 'flex';
            document.getElementById('ai-img-name').textContent = `📎 ${file.name}`;
        };
        reader.readAsDataURL(file);
    }

    window.AIAssistant._clearImage = function () {
        _pendingImage = null;
        _imgPreview.style.display = 'none';
        if (_imgInput) _imgInput.value = '';
    };

    /* ── Send message ─────────────────────────────────────────────────────── */
    async function _sendMessage() {
        const text = (_input.value || '').trim();
        if (!text && !_pendingImage) return;

        const lang = _langSelect ? _langSelect.value : '';
        let prompt = text;
        if (lang) {
            prompt = `Translate the following to ${lang} (keep the meaning accurate, respond ONLY with the translation):\n${text}`;
        }

        _appendMsg('user', text || '(image attached)');
        _input.value = '';

        // Show typing indicator
        const typingId = _appendMsg('ai', '⏳ Thinking…', true);

        try {
            const chatCtx = _chatGetter ? _chatGetter() : [];

            const body = {
                prompt:  prompt,
                context: chatCtx,
            };
            if (_pendingImage) {
                body.image      = _pendingImage.b64;
                body.image_mime = _pendingImage.mime;
            }

            const res  = await fetch('/ai/chat', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(body),
            });
            const data = await res.json();

            _removeMsg(typingId);
            
            if (data.reply) {
                _appendMsg('ai', data.reply);
            }

            // Handle reminder from backend
            if (data.reminder && data.reminder.delay_seconds >= 0) {
                _scheduleReminder(data.reminder.message, data.reminder.delay_seconds * 1000);
            }

        } catch (err) {
            _removeMsg(typingId);
            _appendMsg('ai', '❌ Could not reach AI. Check your connection.');
        }

        window.AIAssistant._clearImage();
    }

    /* ── Quick prompt shortcuts ───────────────────────────────────────────── */
    window.AIAssistant._quick = function (prompt) {
        if (_input) { _input.value = prompt; _sendMessage(); }
    };

    /* ── Reminder scheduling ──────────────────────────────────────────────── */
    function _scheduleReminder(message, delayMs) {
        // Use the actual role as the sender so messages bubble on the correct side
        const chatSender = _role; // 'Patient' or 'Caregiver'

        async function _doSend(text) {
            try {
                const res = await fetch('/chat/send', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ sender: chatSender, text }),
                });
                const data = await res.json();
                if (data.status === 'error') {
                    _appendMsg('ai', `❌ Could not send: ${data.message || 'unknown error'}`);
                } else {
                    // Refresh chat so the sent message appears immediately
                    if (typeof refreshChat === 'function') refreshChat();
                    else if (typeof updateChat === 'function') updateChat();
                }
            } catch (e) {
                _appendMsg('ai', '❌ Failed to send message to chat.');
            }
        }

        if (delayMs <= 0) {
            // Instant send
            _doSend(message);
            return;
        }

        // Timed reminder — show countdown
        const mins = Math.floor(delayMs / 60000);
        const secs = Math.floor((delayMs % 60000) / 1000);
        let timeStr = '';
        if (mins > 0) timeStr += `${mins} min `;
        if (secs > 0 || mins === 0) timeStr += `${secs} sec`;
        _appendMsg('ai', `⏰ Reminder set — "${message}" will be sent in ${timeStr.trim()}.`);

        setTimeout(() => _doSend(`⏰ ${message}`), delayMs);
    }

    /* ── Log rendering helpers ────────────────────────────────────────────── */
    function _appendMsg(role, text, isTemp) {
        const id  = 'aim-' + Date.now() + Math.random().toString(36).slice(2);
        const div = document.createElement('div');
        div.className = `ai-msg ai-msg-${role}`;
        div.id        = id;
        div.innerHTML = `<span class="ai-msg-bubble">${_escHtml(text)}</span>`;
        if (_log) {
            _log.appendChild(div);
            _log.scrollTop = _log.scrollHeight;
        }
        if (!isTemp && _historyLoaded) {
            _history.push({role, text, ts: Date.now()});
            _saveHistory();
        }
        return id;
    }

    function _removeMsg(id) {
        const el = document.getElementById(id);
        if (el) el.remove();
    }

    function _escHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');
    }

    /* ── Injected CSS ─────────────────────────────────────────────────────── */
    function _injectStyles() {
        if (document.getElementById('ai-assist-styles')) return;
        const s = document.createElement('style');
        s.id = 'ai-assist-styles';
        s.textContent = `
/* ── AI trigger button ── */
#ai-assist-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    padding: 6px 0;
    margin-bottom: 4px;
    background: linear-gradient(135deg, rgba(99,102,241,0.18), rgba(168,85,247,0.18));
    border: 1px solid rgba(168,85,247,0.45);
    border-radius: 10px;
    color: #c4b5fd;
    font-size: 0.85rem;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.5px;
    transition: background 0.25s, box-shadow 0.25s;
    gap: 6px;
}
#ai-assist-btn::after { content: ' AI Assistant'; }
#ai-assist-btn:hover,
#ai-assist-btn.ai-btn-active {
    background: linear-gradient(135deg, rgba(99,102,241,0.35), rgba(168,85,247,0.35));
    box-shadow: 0 0 14px rgba(168,85,247,0.4);
}

/* ── Panel ── */
#ai-panel {
    position: fixed;
    bottom: 0; right: 20px;
    width: 360px;
    max-height: 520px;
    background: linear-gradient(145deg, #1a1f35, #0f1628);
    border: 1px solid rgba(168,85,247,0.35);
    border-bottom: none;
    border-radius: 18px 18px 0 0;
    display: flex;
    flex-direction: column;
    z-index: 9999;
    transform: translateY(110%);
    transition: transform 0.35s cubic-bezier(0.4,0,0.2,1);
    box-shadow: 0 -8px 40px rgba(99,102,241,0.25);
}
#ai-panel.ai-panel-open { transform: translateY(0); }

/* ── Header ── */
#ai-panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid rgba(168,85,247,0.2);
    font-weight: 700;
    font-size: 0.9rem;
    color: #c4b5fd;
    background: rgba(99,102,241,0.1);
    border-radius: 18px 18px 0 0;
    cursor: grab;
    touch-action: none;
}
#ai-panel-header:active { cursor: grabbing; }
#ai-lang-select {
    background: rgba(15,22,40,0.9);
    border: 1px solid rgba(168,85,247,0.3);
    color: #c4b5fd;
    border-radius: 6px;
    padding: 3px 6px;
    font-size: 0.78rem;
    cursor: pointer;
}
#ai-close-btn {
    background: none;
    border: none;
    color: #94a3b8;
    font-size: 1rem;
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 4px;
    transition: color 0.2s;
}
#ai-close-btn:hover { color: #ef4444; }

/* ── Chat log ── */
#ai-log {
    flex: 1;
    overflow-y: auto;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.ai-msg { display: flex; }
.ai-msg-user  { justify-content: flex-end; }
.ai-msg-ai    { justify-content: flex-start; }
.ai-msg-bubble {
    max-width: 82%;
    padding: 8px 12px;
    border-radius: 14px;
    font-size: 0.83rem;
    line-height: 1.5;
    word-break: break-word;
}
.ai-msg-user .ai-msg-bubble {
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    color: white;
    border-bottom-right-radius: 4px;
}
.ai-msg-ai .ai-msg-bubble {
    background: rgba(255,255,255,0.07);
    color: #e2e8f0;
    border-bottom-left-radius: 4px;
    border: 1px solid rgba(168,85,247,0.15);
}

/* ── Image preview ── */
#ai-img-preview {
    display: none;
    align-items: center;
    gap: 8px;
    padding: 4px 14px;
    font-size: 0.78rem;
    color: #a78bfa;
    background: rgba(99,102,241,0.08);
    border-top: 1px solid rgba(168,85,247,0.15);
}
#ai-img-preview button {
    background: none; border: none; color: #ef4444;
    cursor: pointer; font-size: 0.9rem; padding: 0;
}

/* ── Input row ── */
#ai-input-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 10px 12px;
    border-top: 1px solid rgba(168,85,247,0.2);
    background: rgba(15,22,40,0.6);
}
#ai-attach-btn {
    font-size: 1.2rem;
    cursor: pointer;
    padding: 4px 6px;
    border-radius: 6px;
    color: #94a3b8;
    transition: color 0.2s;
    flex-shrink: 0;
}
#ai-attach-btn:hover { color: #c4b5fd; }
#ai-input {
    flex: 1;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(168,85,247,0.25);
    border-radius: 10px;
    color: white;
    padding: 8px 12px;
    outline: none;
    transition: border-color 0.2s;
    min-width: 0;
}
#ai-input:focus { border-color: rgba(168,85,247,0.6); }
#ai-input::placeholder { color: #475569; }
#ai-send-btn {
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    border: none;
    color: white;
    padding: 8px 14px;
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.82rem;
    cursor: pointer;
    transition: opacity 0.2s;
    flex-shrink: 0;
}
#ai-send-btn:hover { opacity: 0.85; }

/* ── Quick action chips ── */
#ai-quick-btns {
    display: flex;
    gap: 6px;
    padding: 6px 12px 10px;
    flex-wrap: wrap;
    border-top: 1px solid rgba(168,85,247,0.1);
}
.ai-quick {
    background: rgba(99,102,241,0.12);
    border: 1px solid rgba(168,85,247,0.25);
    color: #a78bfa;
    border-radius: 20px;
    padding: 4px 10px;
    font-size: 0.72rem;
    cursor: pointer;
    transition: background 0.2s;
    white-space: nowrap;
}
.ai-quick:hover { background: rgba(99,102,241,0.28); }

/* ── Mobile responsive ── */
@media (max-width: 768px) {
    #ai-panel {
        width: 100%;
        right: 0;
        max-height: 65vh;
        border-radius: 18px 18px 0 0;
    }
    #ai-assist-btn::after { content: ' AI'; }
}
/* ── Embedded mode ── */
#ai-sidebar-container {
    display: flex !important;
    flex-direction: column !important;
    overflow: hidden !important;
}
#ai-sidebar-container #ai-panel {
    position: relative !important;
    width: 100% !important;
    flex: 1 !important;
    height: auto !important;
    max-height: none !important;
    bottom: auto !important;
    right: auto !important;
    border: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;
    display: flex !important;
    flex-direction: column !important;
    min-height: 0 !important;
    z-index: auto !important;
    transform: none !important;
    transition: none !important;
}
#ai-sidebar-container #ai-log {
    flex: 1 !important;
    min-height: 0 !important;
    overflow-y: auto !important;
}

        `;
        document.head.appendChild(s);
    }

})();
