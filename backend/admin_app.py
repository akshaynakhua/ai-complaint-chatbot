# backend/admin_app.py
# Run with (CMD):
#   set ADMIN_USER=admin
#   set ADMIN_PASS=admin
#   set ADMIN_ORIGIN=http://localhost:5174
#   python backend\admin_app.py

import os
from flask import Flask
from flask_cors import CORS

app = Flask(__name__)

ADMIN_ORIGIN = os.environ.get("ADMIN_ORIGIN", "http://localhost:5174")

CORS(
    app,
    supports_credentials=True,
    resources={ r"/admin/*": { "origins": [ADMIN_ORIGIN] } },
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    methods=["GET", "POST", "OPTIONS"],
    max_age=600,
)

from admin.blueprint import bp as admin_bp  # noqa: E402
app.register_blueprint(admin_bp, url_prefix="/admin")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000, debug=True)
