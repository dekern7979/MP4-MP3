import os, subprocess, tempfile, shutil, re, uuid, time
from flask import Flask, render_template_string, request, send_file, jsonify

app = Flask(__name__)

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or ""
YTDLP = os.environ.get("YTDLP_PATH") or shutil.which("yt-dlp") or shutil.which("yt-dlp.exe") or ""
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "bilibili_web_converted")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>B站视频转MP3</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', sans-serif; background: #f5f5f5; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.card { background: #fff; border-radius: 16px; padding: 40px; width: 480px; max-width: 90vw; box-shadow: 0 4px 24px rgba(0,0,0,.08); }
h1 { font-size: 22px; margin-bottom: 8px; color: #1a1a1a; }
p.sub { color: #888; font-size: 14px; margin-bottom: 24px; }
input, button { width: 100%; padding: 12px 16px; border-radius: 10px; font-size: 15px; outline: none; }
input { border: 1.5px solid #e0e0e0; margin-bottom: 12px; transition: border .2s; }
input:focus { border-color: #fb7299; }
button { background: #fb7299; color: #fff; border: none; font-weight: 600; cursor: pointer; transition: background .2s; }
button:hover { background: #f5588a; }
button:disabled { opacity: .6; cursor: not-allowed; }
#status { margin-top: 16px; padding: 12px; border-radius: 10px; display: none; font-size: 14px; }
#status.loading { display: block; background: #f0f7ff; color: #1a73e8; }
#status.done { display: block; background: #e8f5e9; color: #2e7d32; }
#status.error { display: block; background: #fce4ec; color: #c62828; }
.hidden { display: none; }
</style>
</head>
<body>
<div class="card">
<h1>🎵 B站 → MP3</h1>
<p class="sub">粘贴B站视频链接，一键下载音频</p>
<form id="form">
<input type="text" id="url" placeholder="https://www.bilibili.com/video/BV..." required>
<button type="submit" id="btn">开始转换</button>
</form>
<div id="status"></div>
</div>
<script>
document.getElementById('form').onsubmit = async function(e) {
e.preventDefault();
const url = document.getElementById('url').value.trim();
const btn = document.getElementById('btn');
const status = document.getElementById('status');
if (!url) return;
btn.disabled = true;
status.className = 'loading';
status.textContent = '⏳ 正在下载并转换，请稍候...';
status.style.display = 'block';
try {
const res = await fetch('/convert', {
method: 'POST',
headers: {'Content-Type': 'application/json'},
body: JSON.stringify({url})
});
if (!res.ok) {
const err = await res.json();
status.className = 'error';
status.textContent = '❌ ' + (err.error || '转换失败');
return;
}
const blob = await res.blob();
const a = document.createElement('a');
a.href = URL.createObjectURL(blob);
a.download = 'bilibili_audio.mp3';
a.click();
URL.revokeObjectURL(a.href);
status.className = 'done';
status.textContent = '✅ 下载完成！';
} catch (e) {
status.className = 'error';
status.textContent = '❌ 网络错误：' + e.message;
} finally {
btn.disabled = false;
}
};
</script>
</body>
</html>"""

def sanitize_filename(s):
    s = re.sub(r'[\\/*?:"<>|]', '', s)
    return s[:64] or "bilibili"

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/convert", methods=["POST"])
def convert():
    data = request.get_json()
    raw = data.get("url", "").strip()
    m = re.search(r'https?://[^\s<>"\']+', raw)
    url = m.group(0) if m else ""
    if not url:
        return jsonify({"error": "未找到有效的视频链接(需包含 http:// 或 https://)"}), 400

    task_id = str(uuid.uuid4())[:8]
    temp_dir = os.path.join(tempfile.gettempdir(), f"bili_{task_id}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        def download(fmt, extra_args=None):
            cmd = [YTDLP, "-f", fmt, "-o", os.path.join(temp_dir, "%(id)s.%(ext)s"),
                   "--no-playlist", "--print", "after_move:filepath"]
            if extra_args:
                cmd.extend(extra_args)
            cmd.append(url)
            return subprocess.run(cmd, capture_output=True, text=True, timeout=150)

        result = download("bestaudio")
        if result.returncode != 0:
            result = download("best")
        if result.returncode != 0:
            strategies = [
                ["--cookies-from-browser", "chromium"],
                ["--cookies-from-browser", "chrome"],
                ["--extractor-args", "douyin:app_version=33.0.0"],
                ["--extractor-args", "douyin:web_version=latest"],
                ["--add-header", "Referer:https://www.douyin.com/"],
            ]
            for args in strategies:
                if result.returncode != 0:
                    result = download("best", args)
        if result.returncode != 0:
            return jsonify({"error": f"下载失败: {result.stderr[-200:]}"}), 500

        files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
        if not files:
            return jsonify({"error": "未找到下载的文件"}), 500
        media_file = os.path.join(temp_dir, files[0])

        mp3_path = os.path.join(DOWNLOAD_DIR, f"bilibili_{task_id}.mp3")
        subprocess.run(
            [FFMPEG, "-i", media_file, "-vn", "-codec:a", "libmp3lame", "-q:a", "2", "-y", mp3_path],
            capture_output=True, text=True, timeout=180
        )

        if not os.path.isfile(mp3_path):
            return jsonify({"error": "转换失败"}), 500

        return send_file(mp3_path, as_attachment=True, download_name="bilibili_audio.mp3")

    except subprocess.TimeoutExpired:
        return jsonify({"error": "处理超时，视频可能过长"}), 500
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    print("=" * 50)
    print("  B站视频转MP3 网页版")
    print("=" * 50)
    print(" 启动成功！")
    print(" 本机访问: http://127.0.0.1:5000")
    import socket
    host = socket.gethostbyname(socket.gethostname())
    print(f" 局域网访问: http://{host}:5000")
    print(" (让朋友连同一个WiFi即可访问)")
    print(" 按 Ctrl+C 停止服务")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
