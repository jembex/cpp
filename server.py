from flask import Flask, jsonify

app = Flask(__name__)

# This is our "Endpoint"
@app.route('/hello', methods=['GET'])
def say_hello():
    # This data is sent back to the client
    return jsonify({"message": "Hello from the Server!", "status": "success"})

if __name__ == '__main__':
    app.run(host='localhost', port=5000)