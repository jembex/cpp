from flask import Flask, jsonify, request

app = Flask(__name__)

# Storage for chat and admin alerts
chat_history = []
admin_alerts = []

@app.route('/join', methods=['POST'])
def join():
    # Capture the client's IP address
    client_ip = request.remote_addr 
    user = request.json.get("user", "Unknown")
    
    # Create an alert for the Admin
    alert = f"ALERT: User '{user}' joined from IP: {client_ip}"
    admin_alerts.append(alert)
    
    return jsonify({"status": "joined", "ip": client_ip})

@app.route('/send', methods=['POST'])
def send():
    data = request.json
    chat_history.append({"user": data['user'], "message": data['message']})
    return jsonify({"status": "success"})

@app.route('/admin/alerts', methods=['GET'])
def get_alerts():
    # Return all alerts and clear the list so admin only sees new ones
    alerts = list(admin_alerts)
    admin_alerts.clear()
    return jsonify(alerts)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
