"""Flask application entrypoint."""
from flask import Flask, jsonify

app = Flask(__name__)


@app.route('/health')
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy"})


@app.route('/')
def index():
    """Index page."""
    return jsonify({"message": "Welcome to Flask Service"})


if __name__ == '__main__':
    app.run(debug=True)
