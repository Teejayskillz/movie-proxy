# app.py
from flask import Flask, request, jsonify, Response
import sqlite3
import secrets
import os
import hashlib
import time
import glob
import requests
from playwright.sync_api import sync_playwright
import re

# === Configuration ===
CACHE_DIR = 'cache'
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days
DB_PATH = 'downloads.db'
BASE_URL = 'https://proxy.jaraflix.com'

app = Flask(__name__)

# === Auto-migrate DB to add file_size column ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS downloads (
            id TEXT PRIMARY KEY,
            original_url TEXT NOT NULL,
            renamed_filename TEXT NOT NULL,
            generated_link TEXT NOT NULL,
            downloads INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            file_size TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Run on startup
init_db()

# === Helper Functions ===
def get_cache_path(original_url):
    """Generate a safe cache filename from URL."""
    return os.path.join(CACHE_DIR, hashlib.md5(original_url.encode()).hexdigest() + '.cache')

def is_cache_valid(cache_path):
    """Check if cached file exists and is within TTL."""
    if not os.path.exists(cache_path):
        return False
    age = time.time() - os.path.getmtime(cache_path)
    return age < CACHE_TTL_SECONDS

def clean_old_cache():
    """Delete cache files older than TTL."""
    if not os.path.exists(CACHE_DIR):
        return
    now = time.time()
    for path in glob.glob(os.path.join(CACHE_DIR, '*.cache')):
        if now - os.path.getmtime(path) > CACHE_TTL_SECONDS:
            os.remove(path)

def generate_id(length=6):
    return secrets.token_hex(length // 2)[:length]
def get_wildshare_download_link(source_url: str) -> tuple[str, dict, str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process"
            ]
        )
        context = browser.new_context(
            extra_http_headers={
                "Referer": "https://wildshare.net/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        page = context.new_page()

        # Step 1: Load info page
        print(f"[Scraping] Loading info page: {source_size}")
        page.goto(source_url)
        page.wait_for_load_state("networkidle")

        # Extract file size
        file_size = "Unknown"
        try:
            content = page.content()
            size_match = re.search(r'Size:\s*\(([\d.]+\s*[MKG]B)\)', content, re.IGNORECASE)
            if size_match:
                file_size = size_match.group(1)
        except Exception as e:
            print(f"[Scraping] Size extraction failed: {e}")

        # Step 2: Get pt URL
        button = page.query_selector("span.wildbutton")
        if not button:
            browser.close()
            raise Exception("Download button not found")
        onclick = button.get_attribute("onclick")
        match = re.search(r"window\.location\s*=\s*['\"]([^'\"]+)['\"]", onclick)
        if not match:
            browser.close()
            raise Exception("Could not extract pt URL")
        pt_url = match.group(1)
        print(f"[Scraping] Navigating to pt URL: {pt_url}")

        # Step 3: Go to pt URL and wait for final redirect
        page2 = context.new_page()
        page2.goto(pt_url, wait_until="domcontentloaded", timeout=30000)
        
        # Wait for possible JS redirect
        time.sleep(3)
        final_url = page2.url
        print(f"[Scraping] Final URL after redirect: {final_url}")

        cookies = context.cookies()
        cookie_dict = {c['name']: c['value'] for c in cookies}

        browser.close()
        return final_url, cookie_dict, file_size

# === Routes ===
@app.route('/')
def home():
    return '''
<!DOCTYPE html>
<html>
<head>
    <title>JARAFLIX Movie Proxy</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            max-width: 600px;
            margin: 40px auto;
            padding: 20px;
            background: #f9f9f9;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input[type="text"] {
            width: 100%;
            padding: 10px;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-sizing: border-box;
        }
        button {
            background: #4CAF50;
            color: white;
            padding: 12px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            width: 100%;
            font-size: 16px;
        }
        button:hover {
            background: #45a049;
        }
        button:disabled {
            background: #cccccc;
            cursor: not-allowed;
        }
        #result {
            margin-top: 20px;
            padding: 15px;
            background: white;
            border-radius: 4px;
            display: none;
        }
        #result a {
            color: #1a73e8;
            text-decoration: none;
        }
        #result a:hover {
            text-decoration: underline;
        }
        .error {
            color: #d32f2f;
            background: #ffebee;
        }
    </style>
</head>
<body>
    <h1>🎥 JARAFLIX Movie Proxy</h1>
    <form id="submitForm">
        <div class="form-group">
            <label for="url">Source URL (e.g. WildShare)</label>
            <input type="text" id="url" name="url" placeholder="https://wildshare.net/..." required>
        </div>
        <div class="form-group">
            <label for="filename">Custom Filename</label>
            <input type="text" id="filename" name="filename" placeholder="Tulsa.King.S03E01.mkv" required>
        </div>
        <button type="submit" id="submitBtn">Generate Download Link</button>
    </form>

    <div id="result"></div>

    <script>
        document.getElementById('submitForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            const result = document.getElementById('result');
            btn.disabled = true;
            btn.textContent = 'Processing...';
            result.style.display = 'none';

            const formData = {
                url: document.getElementById('url').value,
                filename: document.getElementById('filename').value
            };

            try {
                const res = await fetch('/submit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });

                const data = await res.json();

                if (res.ok) {
                    result.innerHTML = `
                        <strong>✅ Success!</strong><br>
                        <a href="${data.generated_link}" target="_blank">${data.generated_link}</a><br>
                        <small>Visit this page to download with filename: <code>${data.renamed_filename}</code></small>
                    `;
                    result.className = '';
                } else {
                    result.innerHTML = `<strong>❌ Error:</strong> ${data.error || 'Unknown error'}`;
                    result.className = 'error';
                }
            } catch (err) {
                result.innerHTML = `<strong>❌ Network Error:</strong> ${err.message}`;
                result.className = 'error';
            } finally {
                result.style.display = 'block';
                btn.disabled = false;
                btn.textContent = 'Generate Download Link';
            }
        });
    </script>
</body>
</html>
'''

@app.route('/submit', methods=['POST'])
def submit():
    data = request.get_json()
    original_url = data.get('url')
    renamed_filename = data.get('filename', 'download.mkv')

    if not original_url:
        return jsonify({"error": "Missing 'url'"}), 400

    file_id = generate_id()
    generated_link = f"{BASE_URL}/download-page/{file_id}"

    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT INTO downloads (id, original_url, renamed_filename, generated_link)
        VALUES (?, ?, ?, ?)
    ''', (file_id, original_url, renamed_filename, generated_link))
    conn.commit()
    conn.close()

    return jsonify({
        "id": file_id,
        "original_url": original_url,
        "renamed_filename": renamed_filename,
        "generated_link": generated_link
    })

from flask import render_template  # Add this at the top if not imported

@app.route('/download-page/<id>')
def download_page(id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT renamed_filename, file_size FROM downloads WHERE id = ?", (id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return "File not found", 404

    filename, file_size = row
    if not file_size or file_size == "Unknown":
        file_size = "Unknown size"
    
    download_url = f"{BASE_URL}/download/{id}"

    return render_template('download_page.html', 
                         filename=filename, 
                         file_size=file_size, 
                         download_url=download_url)

@app.route('/download/<id>')
def download(id):
    clean_old_cache()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT original_url, renamed_filename, file_size FROM downloads WHERE id = ?", (id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return "File not found", 404

    original_url, filename, existing_file_size = row

    download_url = original_url
    cookies = {}
    scraped_file_size = existing_file_size  # default

    if 'wildshare.net' in original_url and '?' not in original_url:
        try:
            print(f"[Info] Scraping WildShare for fresh token: {original_url}")
            download_url, cookies, scraped_file_size = get_wildshare_download_link(original_url)
            print(f"[Info] Final URL: {download_url}, Size: {scraped_file_size}")

            # Save size to DB for future use
            conn2 = sqlite3.connect(DB_PATH)
            conn2.execute("UPDATE downloads SET file_size = ? WHERE id = ?", (scraped_file_size, id))
            conn2.commit()
            conn2.close()
        except Exception as e:
            return f"Failed to scrape WildShare: {str(e)}", 500

    # Serve from cache if valid
    cache_path = get_cache_path(download_url)
    if is_cache_valid(cache_path):
        file_size = str(os.path.getsize(cache_path))
        def stream_from_cache():
            with open(cache_path, 'rb') as f:
                while chunk := f.read(8192):
                    yield chunk
        return Response(
            stream_from_cache(),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": file_size,
                "Content-Type": "video/x-matroska"
            }
        )

    # Stream and cache
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        headers = {
            "Referer": "https://wildshare.net/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        is_tokenized = 'wildshare.net' in download_url and 'pt=' in download_url
        resp = requests.get(download_url, stream=True, timeout=30, headers=headers, cookies=cookies)
        if resp.status_code != 200:
            return f"Remote server returned {resp.status_code}", 502

        file_size = resp.headers.get('Content-Length', None)
        if file_size and not file_size.isdigit():
            file_size = None

        response_headers = {
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
        if file_size:
            response_headers["Content-Length"] = file_size
        response_headers["Content-Type"] = resp.headers.get("Content-Type", "video/x-matroska")

        def stream_and_cache():
            with open(cache_path, 'wb') as cache_file:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
                        cache_file.write(chunk)

        return Response(
            stream_and_cache(),
            headers=response_headers
        )

    except Exception as e:
        if os.path.exists(cache_path):
            os.remove(cache_path)
        return f"Download failed: {str(e)}", 500

# === Run App ===
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)