from flask import Flask, jsonify, request

app = Flask(__name__)

# List to store all chat messages
chat_history = []

@app.route('/chat', methods=['POST'])
def post_message():
    data = request.json
    # Expected data: {"user": "...", "message": "...", "role": "client/admin"}
    chat_history.append(data)
    return jsonify({"status": "success"})

@app.route('/chat', methods=['GET'])
def get_messages():
    # Returns the entire chat history
    return jsonify(chat_history)

if __name__ == '__main__':
    # host='0.0.0.0' allows external connections on Render
    app.run(host='0.0.0.0', port=10000)
