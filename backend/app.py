# backend/app.py
import logging, os
from flask import Flask
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB
    CORS(app, supports_credentials=True,
         resources={
             r"/chat": {"origins": ["http://127.0.0.1:*", "http://localhost:*", "null", "*"]},
             r"/uploads/*": {"origins": ["http://127.0.0.1:*", "http://localhost:*", "null", "*"]},
             r"/meta/*": {"origins": ["http://127.0.0.1:*", "http://localhost:*", "null", "*"]},
         },
         allow_headers=["Content-Type", "X-Requested-With"],
         methods=["GET", "POST", "OPTIONS"],
         max_age=600)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    logging.getLogger("complaint-bot").info("App booting…")

    # --- register blueprints ---
    from api.chat import bp as chat_bp
    from api.meta import bp as meta_bp
    from api.files import bp as files_bp
    from api.health import bp as health_bp

    app.register_blueprint(chat_bp)
    app.register_blueprint(meta_bp, url_prefix="/meta")
    app.register_blueprint(files_bp)
    app.register_blueprint(health_bp)

    return app

if __name__ == "__main__":
    app = create_app()
    logging.getLogger("complaint-bot").info("Running server…")
    app.run(host="0.0.0.0", port=5000, debug=True)
