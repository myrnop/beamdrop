import datetime
import html
import json
import mimetypes
import os
import secrets
import socket
import threading
import urllib.parse
import zipfile
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from gi.repository import GLib


DISCOVERY_PORT = 8766
APK_PATH = Path.home() / ".local" / "share" / "beamdrop" / "BeamDrop.apk"

UPLOAD_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BeamDrop — Send to Desktop</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; background: #1e1e1e; color: #fff;
         display: flex; flex-direction: column; align-items: center; justify-content: center;
         min-height: 90vh; margin: 0; padding: 20px; text-align: center; }
  .card { background: #2d2d2d; padding: 28px; border-radius: 18px; max-width: 400px; width: 100%;
          box-shadow: 0 10px 30px rgba(0,0,0,0.4); }
  h2 { margin-top: 0; font-size: 1.4rem; }
  input[type=file] { margin: 18px 0; width: 100%; color: #ccc; }
  button { background: #3584e4; color: #fff; border: none; padding: 14px 24px; border-radius: 12px;
           font-size: 1rem; font-weight: 600; width: 100%; cursor: pointer; }
  button:active { background: #1c71d8; }
</style>
</head>
<body>
  <div class="card">
    <h2>Send Files to Linux Desktop</h2>
    <p style="color:#aaa;font-size:0.9rem;">Files save directly to your Downloads folder.</p>
    <form method="POST" enctype="multipart/form-data">
      <input type="file" name="files" multiple required>
      <button type="submit">Upload to Desktop</button>
    </form>
  </div>
</body>
</html>
"""


def _render_text_page_html(desktop_text: str, status_banner: str = "") -> str:
    escaped_text = html.escape(desktop_text)
    desktop_section = ""
    if desktop_text.strip():
        desktop_section = f"""
        <div class="card" style="margin-bottom: 18px;">
          <h2>From Linux Desktop</h2>
          <textarea id="deskText" readonly rows="5">{escaped_text}</textarea>
          <button type="button" onclick="copyText()">Copy Text</button>
          <p id="copyMsg" style="color:#57e389;font-size:0.85rem;margin-bottom:0;display:none;">Copied to clipboard!</p>
        </div>
        """

    banner_html = (
        f"<p style='color:#57e389;font-weight:600;margin-top:0;'>{html.escape(status_banner)}</p>"
        if status_banner else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BeamDrop — Text & Links</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #1e1e1e; color: #fff;
         display: flex; flex-direction: column; align-items: center; justify-content: center;
         min-height: 90vh; margin: 0; padding: 20px; text-align: center; }}
  .card {{ background: #2d2d2d; padding: 24px; border-radius: 18px; max-width: 420px; width: 100%;
          box-shadow: 0 10px 30px rgba(0,0,0,0.4); box-sizing: border-box; }}
  h2 {{ margin-top: 0; font-size: 1.3rem; }}
  textarea {{ width: 100%; background: #1a1a1a; color: #fff; border: 1px solid #444;
              border-radius: 10px; padding: 12px; font-size: 1rem; box-sizing: border-box;
              margin-bottom: 14px; font-family: inherit; resize: vertical; }}
  button {{ background: #3584e4; color: #fff; border: none; padding: 13px 20px; border-radius: 12px;
           font-size: 1rem; font-weight: 600; width: 100%; cursor: pointer; }}
  button:active {{ background: #1c71d8; }}
</style>
</head>
<body>
  {desktop_section}
  <div class="card">
    <h2>Send Text or Link to Desktop</h2>
    {banner_html}
    <form method="POST">
      <textarea name="snippet" rows="5" placeholder="Paste a URL, code snippet, or note..." required></textarea>
      <button type="submit">Send to Desktop</button>
    </form>
  </div>
  <script>
    function copyText() {{
      const el = document.getElementById('deskText');
      el.select();
      navigator.clipboard.writeText(el.value);
      document.getElementById('copyMsg').style.display = 'block';
    }}
  </script>
</body>
</html>
"""


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


class HttpStreamBuffer:
    def __init__(self, wfile):
        self.wfile = wfile
        self._pos = 0

    def write(self, data: bytes) -> int:
        if data:
            self.wfile.write(data)
            self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return False

    def flush(self):
        self.wfile.flush()


def _collect_archive_entries(paths: list[Path]) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    for p in paths:
        if p.is_file():
            entries.append((p, p.name))
        elif p.is_dir():
            for root, _, files in os.walk(p):
                root_path = Path(root)
                for fname in sorted(files):
                    full_path = root_path / fname
                    if full_path.is_file():
                        arcname = str(full_path.relative_to(p.parent))
                        entries.append((full_path, arcname))
    return entries


class BeamHttpServer:
    def __init__(self, on_event_cb):
        self.on_event_cb = on_event_cb
        self.token = secrets.token_urlsafe(8)
        self.mode = "send"
        self.shared_paths: list[Path] = []
        self.shared_text: str = ""
        dl = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        self.download_dir = Path(dl) if dl else (Path.home() / "Downloads")
        self.httpd: ThreadingHTTPServer | None = None
        self.port: int = 0
        self._stop_beacon = threading.Event()
        self.connected_devices: dict[str, dict] = {}
        self.pending_pushes: dict[str, list[Path]] = {}
        self._lock = threading.Lock()

    def queue_push_for_device(self, device_key: str, paths: list[Path]):
        valid = [x for x in paths if x.exists()]
        if not valid:
            return
        with self._lock:
            self.pending_pushes[device_key] = valid

    def _emit_transfer(self, direction: str, name: str, target_path: Path | None = None, text_content: str | None = None):
        record = {
            "direction": direction,
            "name": name,
            "path": target_path,
            "text": text_content,
            "time": datetime.datetime.now().strftime("%H:%M"),
        }
        GLib.idle_add(self.on_event_cb, record)

    def _start_udp_beacon(self, lan_ip: str):
        def beacon_loop():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            hostname = socket.gethostname()
            while not self._stop_beacon.is_set():
                payload = json.dumps({
                    "service": "beamdrop-v1",
                    "name": hostname,
                    "ip": lan_ip,
                    "port": self.port,
                    "token": self.token,
                    "mode": self.mode,
                }).encode("utf-8")
                try:
                    sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
                except Exception:
                    pass
                self._stop_beacon.wait(2.0)
            sock.close()

        threading.Thread(target=beacon_loop, daemon=True).start()

    def start(self, preferred_port: int = 8765):
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _check_token(self) -> bool:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if params.get("t", [""])[0] != server_ref.token:
                    self.send_error(403, "Invalid session token")
                    return False
                return True

            def _send_html(self, html_str: str):
                body = html_str.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, data: dict):
                body = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)

                # Public route for Android companion APK download
                if parsed.path == "/app.apk":
                    if not APK_PATH.exists():
                        self.send_error(404, "BeamDrop.apk not found in ~/.local/share/beamdrop/")
                        return
                    size = APK_PATH.stat().st_size
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.android.package-archive")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Content-Disposition", 'attachment; filename="BeamDrop.apk"')
                    self.end_headers()
                    with open(APK_PATH, "rb") as f:
                        while chunk := f.read(1024 * 256):
                            self.wfile.write(chunk)
                    server_ref._emit_transfer("sent", "BeamDrop.apk", target_path=APK_PATH)
                    return

                if not self._check_token():
                    return

                # Android companion handshake & state check
                if parsed.path == "/ping":
                    params = urllib.parse.parse_qs(parsed.query)
                    device_name = params.get("device", ["Android Device"])[0]
                    client_ip = self.client_address[0]
                    device_key = f"{device_name}@{client_ip}"
                    with server_ref._lock:
                        server_ref.connected_devices[device_key] = {
                            "key": device_key,
                            "name": device_name,
                            "ip": client_ip,
                            "last_seen": datetime.datetime.now().timestamp(),
                        }
                        has_push = bool(server_ref.pending_pushes.get(device_key))
                    server_ref._emit_transfer("device", device_name)
                    self._send_json({
                        "status": "ok",
                        "hostname": socket.gethostname(),
                        "mode": server_ref.mode,
                        "shared_text": server_ref.shared_text,
                        "has_files": bool(server_ref.shared_paths),
                        "pending_push": has_push,
                    })
                    return

                # Direct push download for a specific Android device
                if parsed.path == "/push-download":
                    params = urllib.parse.parse_qs(parsed.query)
                    device_name = params.get("device", ["Android Device"])[0]
                    client_ip = self.client_address[0]
                    device_key = f"{device_name}@{client_ip}"
                    with server_ref._lock:
                        paths = server_ref.pending_pushes.pop(device_key, [])
                    paths = [x for x in paths if x.exists()]
                    if not paths:
                        self.send_error(404, "No queued push files")
                        return
                    if len(paths) == 1 and paths[0].is_file():
                        fpath = paths[0]
                        mime, _ = mimetypes.guess_type(str(fpath))
                        mime = mime or "application/octet-stream"
                        size = fpath.stat().st_size
                        quoted_name = urllib.parse.quote(fpath.name)
                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(size))
                        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted_name}")
                        self.end_headers()
                        with open(fpath, "rb") as f:
                            while chunk := f.read(1024 * 256):
                                self.wfile.write(chunk)
                        server_ref._emit_transfer("sent", f"{fpath.name} → {device_name}", target_path=fpath)
                        return

                    entries = _collect_archive_entries(paths)
                    if not entries:
                        self.send_error(404, "Selected folder contains no files")
                        return
                    zip_name = f"{paths[0].name}.zip" if len(paths) == 1 else f"BeamDrop-{len(paths)}-items.zip"
                    quoted_zip = urllib.parse.quote(zip_name)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted_zip}")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    stream_buf = HttpStreamBuffer(self.wfile)
                    with zipfile.ZipFile(stream_buf, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
                        for full_path, arcname in entries:
                            zf.write(full_path, arcname=arcname)
                    stream_buf.flush()
                    server_ref._emit_transfer("sent", f"{zip_name} → {device_name}", target_path=paths[0])
                    return

                if server_ref.mode == "receive":
                    self._send_html(UPLOAD_PAGE_HTML)
                    return

                if server_ref.mode == "text":
                    self._send_html(_render_text_page_html(server_ref.shared_text))
                    return

                paths = [p for p in server_ref.shared_paths if p.exists()]
                if not paths:
                    self.send_error(404, "No files currently shared")
                    return

                if len(paths) == 1 and paths[0].is_file():
                    fpath = paths[0]
                    mime, _ = mimetypes.guess_type(str(fpath))
                    mime = mime or "application/octet-stream"
                    size = fpath.stat().st_size
                    quoted_name = urllib.parse.quote(fpath.name)

                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(size))
                    self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted_name}")
                    self.end_headers()

                    with open(fpath, "rb") as f:
                        while chunk := f.read(1024 * 256):
                            self.wfile.write(chunk)

                    server_ref._emit_transfer("sent", fpath.name, target_path=fpath)
                    return

                entries = _collect_archive_entries(paths)
                if not entries:
                    self.send_error(404, "Selected folder contains no files")
                    return

                zip_name = f"{paths[0].name}.zip" if len(paths) == 1 else f"BeamDrop-{len(paths)}-items.zip"
                quoted_zip = urllib.parse.quote(zip_name)

                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted_zip}")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                stream_buf = HttpStreamBuffer(self.wfile)
                with zipfile.ZipFile(stream_buf, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
                    for full_path, arcname in entries:
                        zf.write(full_path, arcname=arcname)
                stream_buf.flush()

                server_ref._emit_transfer("sent", f"{zip_name} ({len(entries)} files)", target_path=paths[0])

            def do_POST(self):
                if not self._check_token():
                    return

                parsed = urllib.parse.urlparse(self.path)
                content_length = int(self.headers.get("Content-Length", 0))
                content_type = self.headers.get("Content-Type", "")
                raw_body = self.rfile.read(content_length)

                # Allow Android companion to send text regardless of active desktop tab
                if server_ref.mode == "text" or parsed.path == "/text":
                    parsed_form = urllib.parse.parse_qs(raw_body.decode("utf-8", errors="replace"))
                    snippet = parsed_form.get("snippet", [""])[0].strip()
                    if snippet:
                        preview = snippet.replace("\n", " ")[:48]
                        server_ref._emit_transfer("text", preview, text_content=snippet)
                    self._send_html(_render_text_page_html(server_ref.shared_text, status_banner="Sent to desktop!"))
                    return

                # Allow Android companion to upload files via /upload or when in receive mode
                if server_ref.mode != "receive" and parsed.path != "/upload":
                    self.send_error(403, "Upload not enabled")
                    return

                header_bytes = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
                msg = BytesParser(policy=default).parsebytes(header_bytes + raw_body)

                saved_paths: list[Path] = []
                server_ref.download_dir.mkdir(parents=True, exist_ok=True)

                if msg.is_multipart():
                    for part in msg.iter_parts():
                        filename = part.get_filename()
                        if not filename:
                            continue
                        safe_name = os.path.basename(filename)
                        dest = server_ref.download_dir / safe_name
                        counter = 1
                        while dest.exists():
                            dest = server_ref.download_dir / f"{dest.stem}_{counter}{dest.suffix}"
                            counter += 1
                        dest.write_bytes(part.get_payload(decode=True))
                        saved_paths.append(dest)
                        server_ref._emit_transfer("received", dest.name, target_path=dest)

                saved_names = [p.name for p in saved_paths]
                self._send_html(
                    "<html><body style='font-family:sans-serif;background:#1e1e1e;color:#fff;"
                    "text-align:center;padding-top:60px;'>"
                    f"<h2>Uploaded {len(saved_names)} file(s)!</h2>"
                    f"<p>{html.escape(', '.join(saved_names))}</p>"
                    "<a href='' style='color:#62a0ea;'>Send more</a></body></html>"
                )

        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", preferred_port), Handler)
        except OSError:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", 0), Handler)

        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self._start_udp_beacon(_detect_lan_ip())