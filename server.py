from flask import Flask, jsonify, request

app = Flask(__name__)

chat_history = []
admin_alerts = []

@app.route('/join', methods=['POST'])
def join():
    user = request.json.get("user", "Guest")
    client_ip = request.remote_addr
    # Send a notification to the Admin only
    admin_alerts.append(f"JOINED: {user} (IP: {client_ip})")
    return jsonify({"status": "ok"})

@app.route('/chat', methods=['GET', 'POST'])
def handle_chat():
    if request.method == 'POST':
        # Add new message to the history
        chat_history.append(request.json)
        return jsonify({"status": "sent"})
    # Send full chat history back to the client
    return jsonify(chat_history)

@app.route('/admin/view', methods=['GET'])
def admin_view():
    # Admin checks this to see join notifications
    alerts = list(admin_alerts)
    admin_alerts.clear() # Clear after reading
    return jsonify(alerts)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
