# Demo: Adaptive Bitrate Streaming (HLS)

Demo funcional de cómo Netflix / YouTube / Amazon entregan video adaptándose a tu red.

## Qué hace

1. Toma tu video MP4 y lo transcodifica a **4 calidades** (240p, 480p, 720p, 1080p) en formato HLS.
2. Sirve los segmentos desde un servidor HTTP local con **throttling configurable** (simula redes lentas).
3. El player en `index.html` usa `hls.js` y **cambia automáticamente de calidad** según el ancho de banda disponible — y muestra todo lo que pasa por debajo (calidad activa, bitrate, buffer, segmentos descargados).

## Uso

```bash
pip install imageio-ffmpeg
python prepare.py        # transcodifica (1-3 min)
python prepare-cmaf.py   # genera CMAF: HLS + DASH sobre los mismos .m4s
python server.py         # sirve en http://localhost:8000
```

Abre `http://localhost:8000/` y prueba los botones de "Simular red". En 5-10 segundos verás cómo el player baja o sube de calidad.

## Por qué NO se mezclan frames de baja resolución entre frames originales

La idea inicial fue: "si la red va mal, mando algunos frames en 440p entre los originales". Eso no funciona porque:

- **Los codecs son predictivos**. H.264/H.265 codifican P-frames y B-frames como deltas contra otros frames. Si metes un frame de menor resolución en medio, rompes la cadena de referencia y el decoder produce basura.
- **El bitrate no se reduce de forma proporcional** al cambiar solo algunos frames — los I-frames (keyframes) son los que pesan, y los intermedios ya son delta-compressed.
- **Los artefactos visuales** serían muy notorios (parpadeo de resolución, "popping").

Lo que las plataformas hacen es **cambiar de stream completo** en límites de keyframe (cada 2-6 segundos). El video se pre-codifica en N versiones independientes; el player descarga la que corresponde a la red actual y empalma en el siguiente keyframe. Los empalmes son invisibles porque cada segmento empieza con un I-frame.

## Arquitectura

```
              prepare.py (ffmpeg)
                     |
                     v
   stream/master.m3u8  <-- multivariant playlist
   stream/240p/playlist.m3u8 + segmentos .ts
   stream/480p/...
   stream/720p/...
   stream/1080p/...
                     ^
                     | HTTP (con throttling)
                     |
              server.py
                     ^
                     |
               index.html
              (hls.js ABR)
```

## Qué observar en la UI

- **Calidad actual**: cambia sola al apretar un botón de red.
- **Bandwidth estimado** (línea azul en la gráfica): el player mide en cada segmento descargado.
- **Buffer disponible** (línea verde): cuántos segundos de video tienes por delante. Si baja a 0, se congela; el ABR trata de mantenerlo arriba.
- **Último segmento**: ves la URL real (`240p/seg_00012.ts` vs `1080p/seg_00012.ts`) — confirma que sí está cambiando de stream.

## Tip: throttling más realista

El throttling de `server.py` limita ancho de banda. Si quieres simular además **latencia** y **jitter**, abre DevTools → pestaña Network → menú de throttling → "Slow 3G" / "Fast 3G". Los dos métodos son complementarios.

## GitHub Pages

El demo también funciona como sitio estático en GitHub Pages. En ese modo se sirven los manifiestos y segmentos desde el CDN de GitHub Pages, por lo que:

- La sección CMAF reproduce HLS y DASH sobre los mismos `.m4s`.
- El QR apunta a la URL pública del sitio.
- El throttling por botones no está disponible porque requiere `server.py`; para simular red en Pages usa DevTools → Network throttling.
