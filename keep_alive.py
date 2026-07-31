import os
import logging
from flask import Flask
from threading import Thread

app = Flask('')

# Tắt log cảnh báo phát triển thừa từ Werkzeug
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route('/')
def home():
    return "Bot is running!"

def run():
    # Render sẽ tự động cấp một cổng qua biến môi trường PORT (mặc định 8080)
    port = int(os.environ.get("PORT", 8080))
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

