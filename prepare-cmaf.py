"""
Genera el demo de CMAF: un solo set de segmentos fMP4 (.m4s) compartidos
por DOS manifiestos distintos (HLS .m3u8 y DASH .mpd). Demuestra que
mismos archivos binarios = funcionan en Apple, Google, Microsoft, Smart TVs.

Estrategia (mas confiable en Windows que el muxer dash de ffmpeg):
  1) Usa ffmpeg en modo HLS con -hls_segment_type fmp4 -> genera .m4s + .m3u8
  2) Lee los .m4s ya generados y arma manifest.mpd (DASH) manualmente
  3) Resultado: ambos manifiestos referencian los mismos binarios .m4s
"""
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import imageio_ffmpeg

SOURCE = Path(r"C:\Users\ordunama\Downloads\dji_fly_20260420_180052_0034_1776729846680_video.MP4")
OUT_DIR = Path(__file__).parent / "stream-cmaf"

VARIANTS = [
    # (nombre, ancho, alto, bitrate_kbps, max_kbps)
    ("480p",  854,  480, 1200, 1500),
    ("720p", 1280,  720, 2800, 3300),
    ("1080p",1920, 1080, 5000, 6000),
]

SEGMENT_SECONDS = 4
GOP = 60
TIMESCALE = 1000  # ms


def build_command(ffmpeg: str) -> list[str]:
    cmd = [ffmpeg, "-y", "-i", str(SOURCE)]

    splits = "[0:v:0]split={n}{labels}".format(
        n=len(VARIANTS),
        labels="".join(f"[v{i}]" for i in range(len(VARIANTS))),
    )
    scales = ";".join(
        f"[v{i}]scale=w={w}:h={h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2[v{i}out]"
        for i, (_, w, h, *_rest) in enumerate(VARIANTS)
    )
    cmd += ["-filter_complex", splits + ";" + scales]

    for i, (name, w, h, vk, maxk) in enumerate(VARIANTS):
        cmd += [
            "-map", f"[v{i}out]",
            f"-c:v:{i}", "libx264",
            f"-b:v:{i}", f"{vk}k",
            f"-maxrate:v:{i}", f"{maxk}k",
            f"-bufsize:v:{i}", f"{maxk * 2}k",
            "-preset", "veryfast",
            "-profile:v:" + str(i), "main",
            "-level:v:" + str(i), "4.0",
            "-pix_fmt", "yuv420p",
            "-g", str(GOP),
            "-keyint_min", str(GOP),
            "-sc_threshold", "0",
        ]

    var_stream_map = " ".join(f"v:{i},name:{VARIANTS[i][0]}" for i in range(len(VARIANTS)))

    cmd += [
        "-f", "hls",
        "-hls_time", str(SEGMENT_SECONDS),
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_type", "fmp4",
        "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(OUT_DIR / "%v" / "seg_%05d.m4s"),
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", var_stream_map,
        str(OUT_DIR / "%v" / "playlist.m3u8"),
    ]
    return cmd


def fix_paths(out_dir: Path) -> None:
    for f in out_dir.rglob("*.m3u8"):
        txt = f.read_text(encoding="utf-8")
        f.write_text(txt.replace("\\", "/"), encoding="utf-8")


def relocate_init_files(out_dir: Path, script_dir: Path) -> dict[str, str]:
    """
    Bug conocido de ffmpeg: con -var_stream_map + fmp4, los init files
    (init_0.mp4, init_1.mp4, ...) se escriben en el directorio de ejecucion
    en vez de las subcarpetas de cada variante. Los movemos a su lugar y
    devolvemos el mapa {variant_name: init_filename}.
    """
    mapping = {}
    for i, (name, *_rest) in enumerate(VARIANTS):
        src = script_dir / f"init_{i}.mp4"
        dst = out_dir / name / f"init_{i}.mp4"
        if src.exists():
            src.replace(dst)
        mapping[name] = f"init_{i}.mp4"
    return mapping


def parse_segment_durations(playlist: Path) -> list[float]:
    """Lee #EXTINF de un playlist HLS y regresa duraciones en segundos."""
    durations = []
    for line in playlist.read_text(encoding="utf-8").splitlines():
        if line.startswith("#EXTINF:"):
            num = line.split(":", 1)[1].split(",", 1)[0]
            durations.append(float(num))
    return durations


def generate_dash_mpd(out_dir: Path, init_map: dict[str, str]) -> Path:
    """
    Construye manifest.mpd referenciando los MISMOS archivos .m4s que ya
    genero ffmpeg para HLS. Esto es la esencia de CMAF: un solo binario,
    multiples manifiestos.
    """
    # Calcula duracion total y reune metadata por variante
    variants_meta = []
    total_duration = 0.0
    for name, w, h, vk, maxk in VARIANTS:
        playlist = out_dir / name / "playlist.m3u8"
        durations = parse_segment_durations(playlist)
        total = sum(durations)
        if total > total_duration:
            total_duration = total
        variants_meta.append({
            "name": name, "w": w, "h": h, "kbps": vk, "max_kbps": maxk,
            "durations": durations, "total": total,
            "init": init_map[name],
        })

    # Construye XML
    NS = "urn:mpeg:dash:schema:mpd:2011"
    ET.register_namespace("", NS)
    mpd = ET.Element(f"{{{NS}}}MPD", {
        "type": "static",
        "mediaPresentationDuration": f"PT{total_duration:.3f}S",
        "minBufferTime": "PT2.0S",
        "profiles": "urn:mpeg:dash:profile:isoff-on-demand:2011",
    })

    period = ET.SubElement(mpd, f"{{{NS}}}Period", {"start": "PT0.0S"})
    aset = ET.SubElement(period, f"{{{NS}}}AdaptationSet", {
        "mimeType": "video/mp4",
        "contentType": "video",
        "segmentAlignment": "true",
        "startWithSAP": "1",
    })

    for v in variants_meta:
        rep = ET.SubElement(aset, f"{{{NS}}}Representation", {
            "id": v["name"],
            "codecs": "avc1.4d4028",
            "bandwidth": str(v["kbps"] * 1000),
            "width": str(v["w"]),
            "height": str(v["h"]),
            "frameRate": "30000/1001",
        })

        seg_list = ET.SubElement(rep, f"{{{NS}}}SegmentList", {
            "timescale": str(TIMESCALE),
            "duration": str(int(SEGMENT_SECONDS * TIMESCALE)),
        })
        ET.SubElement(seg_list, f"{{{NS}}}Initialization", {
            "sourceURL": f"{v['name']}/{v['init']}",
        })
        for i in range(len(v["durations"])):
            seg_url = f"{v['name']}/seg_{i:05d}.m4s"
            ET.SubElement(seg_list, f"{{{NS}}}SegmentURL", {"media": seg_url})

    tree = ET.ElementTree(mpd)
    ET.indent(tree, space="  ")
    mpd_path = out_dir / "manifest.mpd"
    tree.write(mpd_path, xml_declaration=True, encoding="utf-8")
    return mpd_path


def main() -> int:
    if not SOURCE.exists():
        print(f"[ERROR] No se encontro: {SOURCE}")
        return 1

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    for v in VARIANTS:
        (OUT_DIR / v[0]).mkdir()

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"[INFO] Transcodificando a CMAF (.m4s)...")
    print(f"[INFO] Salida: {OUT_DIR}\n")

    proc = subprocess.run(build_command(ffmpeg))
    if proc.returncode != 0:
        print(f"\n[ERROR] ffmpeg fallo con codigo {proc.returncode}")
        return proc.returncode

    fix_paths(OUT_DIR)
    init_map = relocate_init_files(OUT_DIR, Path(__file__).parent)
    print(f"[INFO] Init files reubicados: {init_map}")

    print("\n[INFO] Generando manifest DASH (.mpd) sobre los mismos archivos...")
    mpd = generate_dash_mpd(OUT_DIR, init_map)
    print(f"[INFO] Generado: {mpd}")

    m4s = list(OUT_DIR.rglob("*.m4s"))
    print(f"\n[OK] CMAF listo:")
    print(f"     {len(m4s)} segmentos .m4s (compartidos por HLS y DASH)")
    print(f"     {OUT_DIR / 'master.m3u8'}     <- HLS manifest")
    print(f"     {OUT_DIR / 'manifest.mpd'}    <- DASH manifest")
    print(f"\n     Mismos binarios, dos manifiestos. Eso es CMAF.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
