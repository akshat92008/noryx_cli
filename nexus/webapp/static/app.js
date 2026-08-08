/**
 * NexusAI Web App — WebSocket chat client with file tree, tool visualization,
 * markdown rendering, and real-time streaming.
 */

// ─── State ──────────────────────────────────────────────────────────────────
let ws = null;
let sessionId = `ws_${Date.now()}`;
let isThinking = false;
let currentModel = 'Loading...';

// ─── DOM Elements ───────────────────────────────────────────────────────────
const chatArea = document.getElementById('chatArea');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
const newChatBtn = document.getElementById('newChatBtn');
const modelSelector = document.getElementById('modelSelector');
const topbarModel = document.getElementById('topbarModel');
const welcomeScreen = document.getElementById('welcomeScreen');
const fileTree = document.getElementById('fileTree');

// ─── Initialize ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
    loadModels();
    loadFileTree();
    setupEventListeners();
    autoResizeInput();
});

// ─── WebSocket Connection ───────────────────────────────────────────────────
function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connected');
        ws.send(JSON.stringify({ type: 'set_session', session_id: sessionId }));
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleMessage(data);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    ws.onclose = () => {
        console.log('WebSocket closed, reconnecting in 2s...');
        setTimeout(connectWebSocket, 2000);
    };
}

function handleMessage(data) {
    switch (data.type) {
        case 'thinking':
            showThinking();
            break;
        case 'tool_call':
            removeThinking();
            appendToolCall(data);
            break;
        case 'response':
            removeThinking();
            appendAssistantMessage(data.content);
            currentModel = data.model || currentModel;
            topbarModel.textContent = currentModel;
            isThinking = false;
            sendBtn.disabled = false;
            chatInput.focus();
            break;
        case 'error':
            removeThinking();
            appendErrorMessage(data.content);
            isThinking = false;
            sendBtn.disabled = false;
            break;
        case 'session_set':
            sessionId = data.session_id;
            break;
        case 'new_session':
            sessionId = data.session_id;
            break;
        case 'model_set':
            currentModel = data.model;
            topbarModel.textContent = currentModel;
            break;
        case 'cleared':
            clearChat();
            break;
    }
}

// ─── Event Listeners ────────────────────────────────────────────────────────
function setupEventListeners() {
    // Send message
    sendBtn.addEventListener('click', sendMessage);

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize textarea
    chatInput.addEventListener('input', autoResizeInput);

    // Sidebar toggle
    sidebarToggle.addEventListener('click', () => {
        sidebar.classList.toggle('collapsed');
    });

    // New chat
    newChatBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'new_chat' }));
        }
        clearChat();
        welcomeScreen.style.display = 'flex';
    });

    // Model selector
    modelSelector.addEventListener('change', (e) => {
        const model = e.target.value;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'set_model', model }));
        }
    });

    // Suggestion cards
    document.querySelectorAll('.suggestion-card').forEach(card => {
        card.addEventListener('click', () => {
            const prompt = card.dataset.prompt;
            chatInput.value = prompt;
            autoResizeInput();
            sendMessage();
        });
    });
}

// ─── Send Message ───────────────────────────────────────────────────────────
function sendMessage() {
    const message = chatInput.value.trim();
    if (!message || isThinking) return;

    // Hide welcome screen
    welcomeScreen.style.display = 'none';

    // Show user message
    appendUserMessage(message);

    // Send via WebSocket
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'chat', message }));
        isThinking = true;
        sendBtn.disabled = true;
    } else {
        appendErrorMessage('Not connected to server. Reconnecting...');
        connectWebSocket();
    }

    // Clear input
    chatInput.value = '';
    autoResizeInput();
}

// ─── Auto-resize Input ─────────────────────────────────────────────────────
function autoResizeInput() {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 200) + 'px';
}

// ─── Chat UI Helpers ────────────────────────────────────────────────────────
function appendUserMessage(text) {
    const div = document.createElement('div');
    div.className = 'message message-user';
    div.innerHTML = `<div class="message-content">${escapeHtml(text)}</div>`;
    chatArea.appendChild(div);
    scrollToBottom();
}

function appendAssistantMessage(text) {
    if (!text) return;

    const div = document.createElement('div');
    div.className = 'message message-assistant';
    div.innerHTML = `
        <div class="message-avatar">N</div>
        <div class="message-content">${renderMarkdown(text)}</div>
    `;
    chatArea.appendChild(div);

    // Add copy buttons to code blocks
    div.querySelectorAll('pre').forEach(addCopyButton);

    scrollToBottom();
}

function appendToolCall(data) {
    const card = document.createElement('div');
    card.className = 'tool-call-card';

    const statusIcon = data.success ? '✅' : '❌';
    const argsStr = typeof data.args === 'object' ? JSON.stringify(data.args, null, 2) : String(data.args);
    const resultClass = data.success ? '' : ' error';

    // Truncate long results for display
    let resultStr = String(data.result || '');
    if (resultStr.length > 2000) {
        resultStr = resultStr.substring(0, 1000) + '\n\n... (' + (resultStr.length - 2000) + ' chars truncated) ...\n\n' + resultStr.substring(resultStr.length - 1000);
    }

    card.innerHTML = `
        <div class="tool-call-header" onclick="this.parentElement.classList.toggle('expanded')">
            <span class="tool-call-icon">🔧</span>
            <span class="tool-call-name">${escapeHtml(data.name)}</span>
            <span class="tool-call-status">${statusIcon}</span>
            <span class="tool-call-toggle">▼</span>
        </div>
        <div class="tool-call-body">
            <div class="tool-call-args">${escapeHtml(argsStr)}</div>
            <div class="tool-call-result${resultClass}">${escapeHtml(resultStr)}</div>
        </div>
    `;

    // Find the last assistant message container, or create a wrapper
    let container = chatArea.querySelector('.tool-calls-container:last-child');
    if (!container) {
        container = document.createElement('div');
        container.className = 'message message-assistant';
        container.innerHTML = `
            <div class="message-avatar">N</div>
            <div class="message-content tool-calls-container"></div>
        `;
        chatArea.appendChild(container);
        container = container.querySelector('.tool-calls-container');
    }

    container.appendChild(card);
    scrollToBottom();
}

function appendErrorMessage(text) {
    const div = document.createElement('div');
    div.className = 'message message-assistant';
    div.innerHTML = `
        <div class="message-avatar" style="background: linear-gradient(135deg, #ff1744, #ff9100);">!</div>
        <div class="message-content" style="color: var(--accent-red);">${escapeHtml(text)}</div>
    `;
    chatArea.appendChild(div);
    scrollToBottom();
}

function showThinking() {
    removeThinking();
    const div = document.createElement('div');
    div.className = 'thinking-indicator';
    div.id = 'thinkingIndicator';
    div.innerHTML = `
        <div class="message-avatar">N</div>
        <div class="thinking-dots">
            <span></span><span></span><span></span>
        </div>
    `;
    chatArea.appendChild(div);
    scrollToBottom();
}

function removeThinking() {
    const el = document.getElementById('thinkingIndicator');
    if (el) el.remove();
}

function clearChat() {
    // Remove all messages but keep welcome screen
    chatArea.querySelectorAll('.message, .thinking-indicator, .tool-calls-container').forEach(el => el.remove());
}

function scrollToBottom() {
    requestAnimationFrame(() => {
        chatArea.scrollTop = chatArea.scrollHeight;
    });
}

// ─── Markdown Rendering ─────────────────────────────────────────────────────
function renderMarkdown(text) {
    if (!text) return '';

    let html = text;

    // Code blocks (```language\ncode\n```) — process first to protect from other rules
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        const langLabel = lang || 'code';
        return `<pre><div class="code-header"><span>${langLabel}</span></div><code class="language-${langLabel}">${escapeHtml(code.trim())}</code></pre>`;
    });

    // Inline code — process before bold/italic to protect backtick content
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold (**text**) — must process before italic to avoid * collision
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic (*text*) — single asterisks, now safe since ** already handled
    html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // Unordered lists — wrap consecutive <li> in <ul>
    html = html.replace(/^[*-] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/((?:<li>.*?<\/li>\n?)+)/g, '<ul>$1</ul>');

    // Ordered lists — wrap consecutive <li> in <ol>
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    html = html.replace(/((?:<li>.*?<\/li>\n?)+)/g, (match) => {
        // Only wrap in <ol> if not already inside <ul>
        if (match.includes('<ul>')) return match;
        return '<ol>' + match + '</ol>';
    });

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Paragraphs (double newline)
    html = html.replace(/\n\n/g, '</p><p>');
    html = '<p>' + html + '</p>';

    // Clean up empty paragraphs and fix nesting
    html = html.replace(/<p>\s*<\/p>/g, '');
    html = html.replace(/<p>(<h[1-6]>)/g, '$1');
    html = html.replace(/(<\/h[1-6]>)<\/p>/g, '$1');
    html = html.replace(/<p>(<pre>)/g, '$1');
    html = html.replace(/(<\/pre>)<\/p>/g, '$1');
    html = html.replace(/<p>(<ul>)/g, '$1');
    html = html.replace(/(<\/ul>)<\/p>/g, '$1');
    html = html.replace(/<p>(<ol>)/g, '$1');
    html = html.replace(/(<\/ol>)<\/p>/g, '$1');

    return html;
}

// ─── Copy Button ────────────────────────────────────────────────────────────
function addCopyButton(preElement) {
    const header = preElement.querySelector('.code-header');
    if (!header) return;

    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.addEventListener('click', () => {
        const code = preElement.querySelector('code');
        if (code) {
            navigator.clipboard.writeText(code.textContent).then(() => {
                btn.textContent = 'Copied!';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.textContent = 'Copy';
                    btn.classList.remove('copied');
                }, 2000);
            });
        }
    });
    header.appendChild(btn);
}

// ─── Load Models ────────────────────────────────────────────────────────────
async function loadModels() {
    try {
        const resp = await fetch('/api/models');
        const data = await resp.json();

        modelSelector.innerHTML = '';
        data.models.forEach(model => {
            const opt = document.createElement('option');
            opt.value = model.key;
            opt.textContent = `${model.name} (${model.category})`;
            if (model.key === data.default) {
                opt.selected = true;
                currentModel = model.name;
                topbarModel.textContent = currentModel;
            }
            modelSelector.appendChild(opt);
        });
    } catch (e) {
        console.error('Failed to load models:', e);
    }
}

// ─── Load File Tree ─────────────────────────────────────────────────────────
async function loadFileTree(path) {
    try {
        const url = path ? `/api/files?path=${encodeURIComponent(path)}` : '/api/files';
        const resp = await fetch(url);
        const data = await resp.json();

        fileTree.innerHTML = '';

        // Parent directory link
        if (data.parent) {
            const parentItem = createFileTreeItem('📁', '..', true, data.parent);
            fileTree.appendChild(parentItem);
        }

        // Items
        data.items.forEach(item => {
            const icon = item.is_dir ? '📁' : getFileIcon(item.name);
            const el = createFileTreeItem(icon, item.name, item.is_dir, item.path);
            fileTree.appendChild(el);
        });
    } catch (e) {
        console.error('Failed to load file tree:', e);
        fileTree.innerHTML = '<div style="padding: 8px; color: var(--text-tertiary); font-size: 12px;">Could not load files</div>';
    }
}

function createFileTreeItem(icon, name, isDir, path) {
    const div = document.createElement('div');
    div.className = `file-tree-item ${isDir ? 'dir' : 'file'}`;
    div.innerHTML = `<span class="icon">${icon}</span><span class="name" title="${escapeHtml(path)}">${escapeHtml(name)}</span>`;

    div.addEventListener('click', () => {
        if (isDir) {
            loadFileTree(path);
        } else {
            // Send a read_file command to the chat
            chatInput.value = `Read the file: ${path}`;
            sendMessage();
        }
    });

    return div;
}

function getFileIcon(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const icons = {
        'py': '🐍', 'js': '📜', 'ts': '📘', 'jsx': '⚛️', 'tsx': '⚛️',
        'html': '🌐', 'css': '🎨', 'json': '📋', 'md': '📝',
        'yml': '⚙️', 'yaml': '⚙️', 'toml': '⚙️', 'ini': '⚙️',
        'sh': '🐚', 'bash': '🐚', 'zsh': '🐚',
        'go': '🔵', 'rs': '🦀', 'java': '☕', 'kt': '🟣',
        'rb': '💎', 'php': '🐘', 'c': '🔧', 'cpp': '🔧', 'h': '🔧',
        'sql': '🗄️', 'dockerfile': '🐳', 'lock': '🔒',
        'txt': '📄', 'log': '📄', 'env': '🔐',
        'svg': '🎨', 'png': '🖼️', 'jpg': '🖼️',
    };
    return icons[ext] || '📄';
}

// ─── Utilities ──────────────────────────────────────────────────────────────
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
