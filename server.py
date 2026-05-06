"""
Servidor HTTP local con throttling de ancho de banda configurable en vivo.
Sirve los archivos del proyecto y limita la velocidad al servir segmentos
.ts y playlists .m3u8 para simular condiciones de red reales.

Endpoint de control:
    POST /api/throttle?kbps=300   -> limita a 300 kbps
    POST /api/throttle?kbps=0     -> sin limite
    GET  /api/throttle            -> consulta valor actual
"""
import http.server
import json
import socket
import socketserver
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).parent
PORT = 8000

# kbps = 0 significa sin limite. Compartido entre threads.
_state_lock = threading.Lock()
_state = {"kbps": 0}


def get_kbps() -> int:
    with _state_lock:
        return _state["kbps"]


def set_kbps(v: int) -> None:
    with _state_lock:
        _state["kbps"] = max(0, int(v))


def get_local_ip() -> str:
    """IP de la maquina en la LAN (la que ven otros dispositivos en el WiFi)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        # Silenciar logs por defecto excepto los nuestros
        pass

    # --- API de control ---
    def _handle_api(self) -> bool:
        parsed = urlparse(self.path)

        if parsed.path == "/api/info":
            self._json(200, {
                "lan_url": f"http://{get_local_ip()}:{PORT}/",
                "local_ip": get_local_ip(),
                "port": PORT,
            })
            return True

        if not parsed.path.startswith("/api/throttle"):
            return False

        if self.command == "GET":
            self._json(200, {"kbps": get_kbps()})
            return True

        if self.command == "POST":
            qs = parse_qs(parsed.query)
            try:
                kbps = int(qs.get("kbps", ["0"])[0])
            except ValueError:
                self._json(400, {"error": "kbps invalido"})
                return True
            set_kbps(kbps)
            print(f"[throttle] {kbps} kbps" if kbps else "[throttle] sin limite")
            self._json(200, {"kbps": get_kbps()})
            return True

        self._json(405, {"error": "metodo no permitido"})
        return True

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._handle_api():
            self.send_error(404)

    def do_GET(self):
        if self._handle_api():
            return
        return self._serve_throttled()

    # --- Serve con throttling ---
    def _serve_throttled(self):
        parsed = urlparse(self.path)
        rel = parsed.path.lstrip("/")
        if rel == "":
            rel = "index.html"

        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return

        if not target.exists() or target.is_dir():
            return super().do_GET()

        # Solo throttling para segmentos y playlists (assets reales del stream)
        throttle = target.suffix.lower() in {".ts", ".m3u8", ".m4s", ".mp4"}
        kbps = get_kbps() if throttle else 0

        size = target.stat().st_size
        ctype = self.guess_type(str(target))
        if target.suffix.lower() == ".mpd":
            ctype = "application/dash+xml"
        elif target.suffix.lower() == ".m4s":
            ctype = "video/iso.segment"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        if kbps <= 0:
            with open(target, "rb") as f:
                try:
                    self.wfile.write(f.read())
                except (BrokenPipeError, ConnectionResetError):
                    pass
            return

        # Throttled write: chunks pequenos con sleep proporcional
        bytes_per_sec = (kbps * 1000) / 8
        chunk = max(1024, int(bytes_per_sec / 20))  # 20 ticks por segundo
        delay = chunk / bytes_per_sec
        with open(target, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                try:
                    self.wfile.write(buf)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(delay)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    if not (ROOT / "stream" / "master.m3u8").exists():
        print("[WARN] No existe stream/master.m3u8")
        print("[WARN] Corre primero:  python prepare.py")
        print()

    lan = get_local_ip()
    with ThreadedServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Servidor accesible en:")
        print(f"  Local:  http://localhost:{PORT}/")
        print(f"  LAN:    http://{lan}:{PORT}/   <- desde TVs, celulares, Xbox en la misma red")
        print(f"\nThrottling: 0 kbps (sin limite). Cambialo desde la pagina o con:")
        print(f"  curl -X POST http://localhost:{PORT}/api/throttle?kbps=300")
        print("Ctrl+C para detener.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nBye.")


if __name__ == "__main__":
    main()
