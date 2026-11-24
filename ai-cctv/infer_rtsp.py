# pip install ultralytics opencv-python requests
import os
import time
import json
import tempfile
import threading
import requests
from pathlib import Path

# Optimasi FFMPEG RTSP: paksa TCP + nonaktifkan buffer internal agar 0 delay
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"
os.environ.setdefault("YOLO_CONFIG_DIR", tempfile.gettempdir())

import cv2
import torch
from ultralytics import YOLO

SOURCE = "rtsp://localhost:8554/kandang-raw"
WEIGHTS = r"D:\project\pmld 2026\Cow-Monitoring-CV\ai-cctv\exp-3.pt"
CONF = 0.4
IMGSZ = 480              # samakan dengan imgsz saat training
SHOW = True              # False jika dijalankan di server tanpa layar
TARGET_STREAM_FPS = 25   # FPS video live ke website & window OpenCV
JPEG_QUALITY = 70        # Kualitas JPEG optimal (cepat & tajam untuk streaming 1280x720)

# Gunakan 127.0.0.1 (bukan localhost) untuk menghindari delay DNS IPv6 di Windows
BACKEND_URL = os.getenv("BOVISION_BACKEND_URL", "http://127.0.0.1:8000")
CAM_ID = os.getenv("BOVISION_CAM_ID", "cam-01")

MAX_ID = 30        # ID yang ditampilkan selalu 1..MAX_ID (juga batas jumlah deteksi per frame)
ID_TTL = 150       # slot ID dibebaskan jika tidak terlihat selama N frame yang diproses


def pick_weights():
    if WEIGHTS and Path(WEIGHTS).is_file():
        return Path(WEIGHTS)
    local_fallback = Path(__file__).resolve().parent / "exp-3.pt"
    if local_fallback.is_file():
        return local_fallback
    cands = list(Path(".").glob("runs/detect/**/weights/best.pt")) + list(Path("models").glob("*.pt"))
    if not cands:
        raise SystemExit("Bobot tidak ditemukan di runs/detect atau models. Isi WEIGHTS manual.")
    return max(cands, key=lambda p: p.stat().st_mtime)


weights_path = pick_weights()
print("Memakai bobot:", weights_path.resolve(), "| diubah:", time.ctime(weights_path.stat().st_mtime))

device = 0 if torch.cuda.is_available() else "cpu"
model = YOLO(str(weights_path))
names = model.names

id_map = {}     # ID asli dari tracker -> slot 1..MAX_ID
slot_last = {}  # slot -> step terakhir terlihat


def expire(step):
    for tid, s in list(id_map.items()):
        if step - slot_last[s] > ID_TTL:
            del id_map[tid]
            del slot_last[s]


def get_slot(tid, step):
    """Petakan ID tracker (bisa terus naik: 41, 57, ...) ke slot 1..MAX_ID."""
    if tid not in id_map:
        used = set(id_map.values())
        free = [s for s in range(1, MAX_ID + 1) if s not in used]
        if free:
            s = free[0]
        else:
            s = min(slot_last, key=slot_last.get)
            for k, v in list(id_map.items()):
                if v == s:
                    del id_map[k]
        id_map[tid] = s
    s = id_map[tid]
    slot_last[s] = step
    return s


COLORS = [(60, 180, 75), (230, 120, 30), (40, 190, 230), (200, 60, 200), (60, 60, 230), (200, 200, 60)]
FONT = cv2.FONT_HERSHEY_SIMPLEX


def draw_detections(frame, dets):
    """Gambar semua box + label (ID, kelas, confidence) di atas frame."""
    vis = frame.copy()
    scale = max(vis.shape[1] / 1280, 0.6)
    th = max(2, int(2 * scale))
    fs = 0.6 * scale
    for slot, cls, name, conf, x1, y1, x2, y2 in dets:
        color = COLORS[cls % len(COLORS)]
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, th)
        text = f"#{slot if slot else '?'} {name} {conf:.2f}"
        (tw, tht), base = cv2.getTextSize(text, FONT, fs, max(1, th - 1))
        ty = y1 - 6 if y1 - tht - 12 > 0 else y1 + tht + 8
        cv2.rectangle(vis, (x1, ty - tht - 6), (x1 + tw + 8, ty + base), color, -1)
        cv2.putText(vis, text, (x1 + 4, ty), FONT, fs, (255, 255, 255), max(1, th - 1), cv2.LINE_AA)
    return vis


# ==============================================================================
# 1. THREAD PEMBACA RTSP TANPA BUFFER (ZERO-LAG LATEST FRAME GRABBER)
# ==============================================================================
class RTSPLatestFrameGrabber:
    """
    Membaca frame dari RTSP secara kontinu di thread terpisah dan selalu membuang
    antrean buffer lama, sehingga frame yang diambil selalu 100% real-time (0 delay).
    """
    def __init__(self, src):
        self.src = src
        self.lock = threading.Lock()
        self.latest_frame = None
        self.frame_id = 0
        self.connected = False
        self.running = True
        threading.Thread(target=self._reader_loop, daemon=True).start()

    def _reader_loop(self):
        while self.running:
            cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                self.connected = False
                print("Gagal membuka stream RTSP, coba lagi 2 detik...")
                time.sleep(2)
                continue

            self.connected = True
            print(f"Terhubung ke stream RTSP ({self.src}) -> Mode Real-Time Zero-Buffer Aktif!")
            while self.running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.connected = False
                    break
                with self.lock:
                    self.latest_frame = frame
                    self.frame_id += 1

            cap.release()
            print("Stream RTSP terputus, menyambung ulang...")
            time.sleep(1.5)

    def get_latest(self):
        with self.lock:
            return self.latest_frame, self.frame_id


# ==============================================================================
# 2. THREAD PENGIRIM KE WEBSITE (PERSISTENT HTTP KEEP-ALIVE < 1ms)
# ==============================================================================
_latest_push_data = None
_push_event = threading.Event()
_push_lock = threading.Lock()


def _backend_push_worker():
    """Menggunakan requests.Session() (HTTP Keep-Alive) agar koneksi TCP tidak dibuat ulang tiap frame."""
    global _latest_push_data
    push_endpoint = f"{BACKEND_URL}/api/yolo/push"
    session = requests.Session()
    while True:
        _push_event.wait()
        _push_event.clear()
        with _push_lock:
            payload = _latest_push_data
            _latest_push_data = None
        if not payload:
            continue
        jpeg_bytes, meta_json = payload
        try:
            session.post(
                push_endpoint,
                data=jpeg_bytes,
                headers={
                    "Content-Type": "image/jpeg",
                    "X-Yolo-Meta": meta_json,
                },
                timeout=1.0,
            )
        except Exception:
            pass


threading.Thread(target=_backend_push_worker, daemon=True).start()


def push_to_website(vis_frame, dets, fps):
    """Encode frame 1280x720 ke JPEG ringan dan jadwalkan kirim ke website."""
    global _latest_push_data
    try:
        h, w = vis_frame.shape[:2]
        if w != 1280 or h != 720:
            web_frame = cv2.resize(vis_frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
        else:
            web_frame = vis_frame
        ok, buf = cv2.imencode(".jpg", web_frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return
        meta = {
            "cam_id": CAM_ID,
            "fps": round(fps, 1),
            "detections": [
                {
                    "slot": slot,
                    "tag_number": f"COW-{slot:02d}" if slot else "COW-??",
                    "cls_id": cls_id,
                    "behavior": name,
                    "confidence": f"{conf * 100:.1f}%",
                    "conf_val": round(conf, 3),
                    "bbox": [x1, y1, x2, y2],
                }
                for (slot, cls_id, name, conf, x1, y1, x2, y2) in dets
            ],
        }
        with _push_lock:
            _latest_push_data = (buf.tobytes(), json.dumps(meta))
        _push_event.set()
    except Exception:
        pass


# ==============================================================================
# 3. THREAD INFERENSI YOLO (TERPISAH DARI RENDER VIDEO AGAR VIDEO TETAP 25 FPS)
# ==============================================================================
grabber = RTSPLatestFrameGrabber(SOURCE)
dets_lock = threading.Lock()
current_dets = []
yolo_fps = 15.0


def _yolo_inference_worker():
    """
    Menjalankan model.track() selalu pada frame paling baru dari RTSP.
    Dengan memisahkan thread YOLO dari thread video, video di website tetap lancar 25 FPS
    tanpa tertahan waktu komputasi CPU/GPU YOLO.
    """
    global current_dets, yolo_fps
    step = 0
    last_processed_id = -1
    last_t = time.time()

    while True:
        frame, fid = grabber.get_latest()
        if frame is None or fid == last_processed_id:
            time.sleep(0.005)
            continue

        last_processed_id = fid
        step += 1

        res = model.track(
            frame, persist=True, tracker="bytetrack.yaml",
            conf=CONF, imgsz=IMGSZ, max_det=MAX_ID, device=device, verbose=False,
        )[0]
        expire(step)

        new_dets = []
        for b in res.boxes:
            tid = int(b.id.item()) if b.id is not None else None
            slot = get_slot(tid, step) if tid is not None else None
            cls_id = int(b.cls.item())
            conf_val = float(b.conf.item())
            x1, y1, x2, y2 = (int(v) for v in b.xyxy[0])
            new_dets.append((slot, cls_id, names[cls_id], conf_val, x1, y1, x2, y2))
            print(f"cow#{slot if slot else '?'}: {names[cls_id]} ({conf_val:.2f})")

        now_t = time.time()
        dt = max(now_t - last_t, 1e-3)
        last_t = now_t
        with dets_lock:
            current_dets = new_dets
            yolo_fps = 0.8 * yolo_fps + 0.2 * (1.0 / dt)


threading.Thread(target=_yolo_inference_worker, daemon=True).start()


# ==============================================================================
# 4. MAIN LOOP: RENDER VIDEO REAL-TIME (25 FPS) KE WEBSITE & WINDOW OPENCV
# ==============================================================================
last_rendered_id = -1
frame_interval = 1.0 / TARGET_STREAM_FPS
stream_fps_ema = float(TARGET_STREAM_FPS)
last_render_t = time.time()

while True:
    t0 = time.time()
    frame, fid = grabber.get_latest()
    if frame is None or fid == last_rendered_id:
        if SHOW:
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        time.sleep(0.005)
        continue

    last_rendered_id = fid
    with dets_lock:
        dets_snapshot = list(current_dets)

    now_t = time.time()
    stream_fps_ema = 0.9 * stream_fps_ema + 0.1 * (1.0 / max(now_t - last_render_t, 1e-3))
    last_render_t = now_t

    # Gambar kotak deteksi terbaru di atas frame live detik ini juga
    vis = draw_detections(frame, dets_snapshot)
    push_to_website(vis, dets_snapshot, stream_fps_ema)

    if SHOW:
        h, w = vis.shape[:2]
        vis_show = cv2.resize(vis, (960, int(h * 960 / w)))
        cv2.imshow("CBVD5 RTSP", vis_show)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            cv2.destroyAllWindows()
            raise SystemExit

    elapsed = time.time() - t0
    if elapsed < frame_interval:
        time.sleep(frame_interval - elapsed)
