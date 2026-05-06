"""
Transcodifica el video fuente a 4 calidades HLS (240p, 480p, 720p, 1080p)
y genera un master playlist multivariant para Adaptive Bitrate Streaming.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

SOURCE = Path(r"C:\Users\ordunama\Downloads\dji_fly_20260420_180052_0034_1776729846680_video.MP4")
OUT_DIR = Path(__file__).parent / "stream"

VARIANTS = [
    # (nombre, ancho, alto, bitrate_video_kbps, bitrate_audio_kbps, max_bitrate_kbps)
    ("240p",  426,  240,  400,  64,  500),
    ("480p",  854,  480, 1200,  96, 1500),
    ("720p", 1280,  720, 2800, 128, 3300),
    ("1080p",1920, 1080, 5000, 128, 6000),
]

SEGMENT_SECONDS = 4
GOP = 60  # keyframe cada ~2s a 30fps


def build_command(ffmpeg: str) -> list[str]:
    cmd = [ffmpeg, "-y", "-i", str(SOURCE)]

    # Generar un filter_complex para escalar a cada variante.
    # Usamos 0:v:0 para tomar solo el primer stream de video (evita la imagen
    # adjunta y los streams de datos del DJI).
    splits = "[0:v:0]split={n}{labels}".format(
        n=len(VARIANTS),
        labels="".join(f"[v{i}]" for i in range(len(VARIANTS))),
    )
    scales = ";".join(
        f"[v{i}]scale=w={w}:h={h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2[v{i}out]"
        for i, (_, w, h, *_rest) in enumerate(VARIANTS)
    )
    filter_complex = splits + ";" + scales
    cmd += ["-filter_complex", filter_complex]

    # Por cada variante: video map + codec params (el video DJI no tiene audio)
    for i, (name, w, h, vk, ak, maxk) in enumerate(VARIANTS):
        cmd += [
            "-map", f"[v{i}out]",
            f"-c:v:{i}", "libx264",
            f"-b:v:{i}", f"{vk}k",
            f"-maxrate:v:{i}", f"{maxk}k",
            f"-bufsize:v:{i}", f"{maxk * 2}k",
            "-preset", "veryfast",
            "-g", str(GOP),
            "-keyint_min", str(GOP),
            "-sc_threshold", "0",
        ]

    # Salida HLS multivariant (sin audio)
    var_stream_map = " ".join(f"v:{i},name:{VARIANTS[i][0]}" for i in range(len(VARIANTS)))
    cmd += [
        "-f", "hls",
        "-hls_time", str(SEGMENT_SECONDS),
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", str(OUT_DIR / "%v" / "seg_%05d.ts"),
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", var_stream_map,
        str(OUT_DIR / "%v" / "playlist.m3u8"),
    ]
    return cmd


def main() -> int:
    if not SOURCE.exists():
        print(f"[ERROR] No se encontro el video fuente: {SOURCE}")
        return 1

    if OUT_DIR.exists():
        print(f"[INFO] Limpiando {OUT_DIR}")
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    for name, *_ in VARIANTS:
        (OUT_DIR / name).mkdir()

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"[INFO] ffmpeg: {ffmpeg}")
    print(f"[INFO] Fuente: {SOURCE}")
    print(f"[INFO] Salida: {OUT_DIR}")
    print("[INFO] Transcodificando a 4 calidades... (puede tardar 1-3 min)\n")

    cmd = build_command(ffmpeg)
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"\n[ERROR] ffmpeg fallo con codigo {proc.returncode}")
        return proc.returncode

    master = OUT_DIR / "master.m3u8"
    if not master.exists():
        print(f"[ERROR] No se genero {master}")
        return 1

    # Normaliza separadores de ruta (Windows escribe \ pero HLS necesita /)
    text = master.read_text(encoding="utf-8")
    master.write_text(text.replace("\\", "/"), encoding="utf-8")

    print("\n[OK] Listo.")
    print(f"     Master playlist: {master}")
    for name, *_ in VARIANTS:
        segs = list((OUT_DIR / name).glob("seg_*.ts"))
        print(f"     {name}: {len(segs)} segmentos")
    print("\nAhora corre:  python server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
