"""
InstaManager — Application Entry Point.

Run this file to start the Flask + SocketIO development server.
"""

from app import create_app, socketio

app = create_app()

if __name__ == "__main__":
    print("\n  [*] InstaManager running at http://localhost:5000\n")
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False,  # Avoid duplicate scheduler starts
        log_output=True,
    )
