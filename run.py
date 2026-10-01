"""Lancement : `python run.py` (développement) — en production, waitress est utilisé."""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    if os.environ.get("FLASK_DEBUG") == "1":
        app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
    else:
        from waitress import serve

        print(f"Studio Réseaux — http://localhost:{port}")
        serve(app, host="0.0.0.0", port=port, threads=8, channel_timeout=600)
