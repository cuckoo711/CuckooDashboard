"""SMTC media state and lyrics service (Netease + QQ Music)."""

from __future__ import annotations

import base64
import colorsys
import io
import json
import logging
import re
import subprocess as _media_sp
import threading
import time
from pathlib import Path

import requests as _requests

from core.config import CONFIG_DIR, SRC_DIR, PROJECT_ROOT
from core.proc import popen_hidden

logger = logging.getLogger("cuckoo.media")

# ============================================================
# SMTC Worker
# ============================================================

_smtc_result = {"status": "idle", "title": "", "artist": "", "progress_ratio": None}
_smtc_lock = threading.Lock()
_smtc_last_update = 0.0
_SMTC_WORKER = str(SRC_DIR / "smtc_worker.py")
_SMTC_PYTHON = str(PROJECT_ROOT / "venv" / "Scripts" / "python.exe")
_smtc_started = False
_smtc_thread: threading.Thread | None = None
_smtc_process: _media_sp.Popen | None = None
_smtc_stop_event = threading.Event()
_smtc_lifecycle_lock = threading.RLock()

# Cover art cache lives in the main process so /api/media JSON stays small.
_cover_lock = threading.Lock()
_cover_state = {
    "key": "",
    "title": "",
    "artist": "",
    "b64": "",
    "mime": "image/jpeg",
    "url": "",            # remote cover (YesPlayMusic picUrl)
    "updated_at": 0.0,    # only bumps when the cover identity/content changes
    "source_token": "",   # stable identity: remote url or file mtime/key
}
_DEFAULT_COVER_PALETTE = {
    "cover_palette_rgb": [146, 162, 224],
    "cover_palette_1": [146, 162, 224],
    "cover_palette_2": [116, 132, 198],
    "cover_palette_3": [184, 198, 235],
    # Backend-owned visual colors consumed directly by /music.
    "cover_theme_rgb": [146, 162, 224],
    "cover_inverse_rgb": [109, 93, 59],
    "spectrum_rgb": [89, 98, 130],
    "spectrum_block_rgb": [109, 93, 59],
}
_cover_art_lock = threading.Lock()
_cover_art_cache = {
    "identity": "",
    "ambient": b"",
    "ambient_mime": "image/jpeg",
    "palette": dict(_DEFAULT_COVER_PALETTE),
}
_pillow_missing_logged = False
_COVER_FILE = PROJECT_ROOT / "data" / "media_cover.bin"
_COVER_META = PROJECT_ROOT / "data" / "media_cover.json"
_YPM_API = "http://127.0.0.1:27232/player"
_ypm_cache = {"ts": 0.0, "ok": False, "data": None}


def _cover_key(title: str, artist: str, album: str = "") -> str:
    return f"{title or ''}\n{artist or ''}\n{album or ''}"


def _set_cover_locked(
    *,
    key: str,
    title: str,
    artist: str,
    source_token: str,
    b64: str | None = None,
    mime: str | None = None,
    url: str | None = None,
    clear_other: bool = False,
) -> bool:
    """Apply cover fields only when identity changes. Returns True if version bumped."""
    prev_token = str(_cover_state.get("source_token") or "")
    prev_key = str(_cover_state.get("key") or "")
    changed = (source_token and source_token != prev_token) or (key and key != prev_key)

    _cover_state["key"] = key or prev_key
    _cover_state["title"] = title or _cover_state.get("title") or ""
    _cover_state["artist"] = artist or _cover_state.get("artist") or ""
    if url is not None:
        _cover_state["url"] = url
    if b64 is not None:
        _cover_state["b64"] = b64
        if mime:
            _cover_state["mime"] = mime
    if clear_other and url is not None and not b64:
        # switching to a new remote identity: drop stale file bytes
        if changed:
            _cover_state["b64"] = ""
    if source_token:
        _cover_state["source_token"] = source_token
    if changed:
        _cover_state["updated_at"] = time.time()
        with _cover_art_lock:
            if _cover_art_cache.get("identity") != (source_token or key):
                _cover_art_cache.update({
                    "identity": "",
                    "ambient": b"",
                    "ambient_mime": "image/jpeg",
                    "palette": dict(_DEFAULT_COVER_PALETTE),
                })
    elif not _cover_state.get("updated_at"):
        _cover_state["updated_at"] = time.time()
    return changed


def _load_cover_file_if_any(title: str, artist: str, album: str = "") -> None:
    """Import async-extracted SMTC cover dumped by smtc_worker."""
    try:
        if not _COVER_FILE.exists() or not _COVER_META.exists():
            return
        meta = json.loads(_COVER_META.read_text(encoding="utf-8"))
        key = str(meta.get("key") or "")
        want = _cover_key(title, artist, album)
        # accept if same track, or worker key empty
        if key and want and key != want:
            # still accept title-only match (album may differ across sources)
            k_title = (key.split("\n") + [""])[0]
            if k_title and k_title != title:
                return
        # If the same track already has a remote cover URL, keep that source
        # stable. Importing the SMTC thumbnail as a second source would toggle
        # source_token between url:/file: on later polls, bump cover_version, and
        # make the browser reload the blurred background even though the album art
        # did not actually change.
        with _cover_lock:
            current_key = str(_cover_state.get("key") or "")
            current_title = str(_cover_state.get("title") or "")
            current_url = str(_cover_state.get("url") or "")
            same_current = (
                (want and current_key == want)
                or (title and current_title == title)
            )
            if current_url and same_current:
                return

        raw = _COVER_FILE.read_bytes()
        if not raw:
            return
        mime = str(meta.get("mime") or "image/jpeg")
        b64 = base64.b64encode(raw).decode("ascii")
        # Stable token: file mtime + size + key (not wall clock on every poll)
        try:
            st = _COVER_FILE.stat()
            token = f"file:{key}:{int(st.st_mtime_ns)}:{st.st_size}"
        except OSError:
            token = f"file:{key}:{meta.get('updated_at')}:{meta.get('size')}"
        with _cover_lock:
            # If we already have the same remote URL cover for this track, keep it
            # unless file token is new.
            _set_cover_locked(
                key=want or key,
                title=title,
                artist=artist,
                source_token=token,
                b64=b64,
                mime=mime,
            )
    except Exception as exc:
        logger.debug("[media] cover file load failed: %s", exc)


def _update_cover_from_worker(info: dict) -> None:
    """Update cover cache from worker payload (URL / file / legacy b64)."""
    title = str(info.get("title") or "")
    artist = str(info.get("artist") or "")
    album = str(info.get("album") or "")
    key = _cover_key(title, artist, album)

    remote_url = str(info.get("cover_url") or "")
    if remote_url:
        with _cover_lock:
            _set_cover_locked(
                key=key,
                title=title,
                artist=artist,
                source_token=f"url:{remote_url}",
                url=remote_url,
                # A new remote identity must not keep serving the previous
                # SMTC thumbnail through the same proxy endpoint.
                clear_other=True,
            )

    if info.get("cover_file") or info.get("cover_changed") or info.get("cover_pending"):
        _load_cover_file_if_any(title, artist, album)

    if info.get("cover_changed") or "cover_b64" in info:
        b64 = str(info.get("cover_b64") or "")
        mime = str(info.get("cover_mime") or "image/jpeg")
        if b64:
            # legacy path — content identity via short hash prefix
            token = f"b64:{key}:{len(b64)}:{b64[:32]}"
            with _cover_lock:
                _set_cover_locked(
                    key=key,
                    title=title,
                    artist=artist,
                    source_token=token,
                    b64=b64,
                    mime=mime if b64 else "image/jpeg",
                )
        # keep media WS payload light
        info.pop("cover_b64", None)
        info.pop("cover_mime", None)


def get_cover_state() -> dict:
    with _cover_lock:
        return dict(_cover_state)


def get_cover_data_url() -> str:
    with _cover_lock:
        b64 = _cover_state.get("b64") or ""
        mime = _cover_state.get("mime") or "image/jpeg"
        title = _cover_state.get("title") or ""
        url = _cover_state.get("url") or ""
    if b64 and title:
        return f"data:{mime};base64,{b64}"
    return url


def get_cover_bytes() -> tuple[bytes | None, str]:
    with _cover_lock:
        b64 = _cover_state.get("b64") or ""
        mime = _cover_state.get("mime") or "image/jpeg"
        title = _cover_state.get("title") or ""
        url = _cover_state.get("url") or ""
    if b64 and title:
        try:
            return base64.b64decode(b64), mime
        except Exception:
            return None, mime
    # optional: fetch remote cover for /api/media/cover consumers
    if url:
        try:
            r = _requests.get(url, timeout=3)
            if r.ok and r.content:
                ctype = r.headers.get("Content-Type") or "image/jpeg"
                return r.content, ctype.split(";")[0].strip()
        except Exception:
            pass
    return None, mime


def _current_cover_identity() -> str:
    with _cover_lock:
        token = str(_cover_state.get("source_token") or "")
        url = str(_cover_state.get("url") or "")
        key = str(_cover_state.get("key") or "")
    if token:
        return token
    if url:
        return f"url:{url}"
    return key


def _clamp_rgb_channel(value) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = 0
    return max(0, min(255, number))


def _normalize_rgb(value, fallback: list[int] | tuple[int, int, int]) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        value = fallback
    return [_clamp_rgb_channel(value[0]), _clamp_rgb_channel(value[1]), _clamp_rgb_channel(value[2])]


def _rgb_to_hls(rgb) -> tuple[float, float, float]:
    r, g, b = _normalize_rgb(rgb, _DEFAULT_COVER_PALETTE["cover_theme_rgb"])
    return colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)


def _hls_to_rgb(hue: float, lightness: float, saturation: float) -> list[int]:
    r, g, b = colorsys.hls_to_rgb(hue % 1.0, max(0.0, min(1.0, lightness)), max(0.0, min(1.0, saturation)))
    return [_clamp_rgb_channel(r * 255), _clamp_rgb_channel(g * 255), _clamp_rgb_channel(b * 255)]


def _spectrum_rgb_from_theme(theme_rgb) -> list[int]:
    """Muted midpoint toward the theme complement for spectrum bars.

    Pure complement is too loud, while using the theme directly is too similar.
    Keep the complementary hue but pull saturation/lightness toward the middle.
    """
    hue, lightness, saturation = _rgb_to_hls(theme_rgb)
    if saturation < 0.08:
        fallback_hue, _, fallback_saturation = _rgb_to_hls(_DEFAULT_COVER_PALETTE["cover_theme_rgb"])
        hue = fallback_hue
        saturation = max(saturation, fallback_saturation * 0.6)
    spectrum_hue = (hue + 0.5) % 1.0
    spectrum_saturation = max(0.18, min(0.42, saturation * 0.62 + 0.08))
    spectrum_lightness = max(0.32, min(0.52, 0.42 + (lightness - 0.5) * 0.32))
    return _hls_to_rgb(spectrum_hue, spectrum_lightness, spectrum_saturation)


def _contrast_rgb_from_spectrum(spectrum_rgb) -> list[int]:
    """High-contrast slider/peak color based on the chosen spectrum color."""
    hue, lightness, saturation = _rgb_to_hls(spectrum_rgb)
    contrast_hue = (hue + 0.5) % 1.0
    contrast_saturation = max(0.34, min(0.68, saturation * 1.18 + 0.08))
    contrast_lightness = 0.72 if lightness < 0.48 else 0.30
    return _hls_to_rgb(contrast_hue, contrast_lightness, contrast_saturation)


def _inverse_rgb_from_theme(theme_rgb, spectrum_rgb=None) -> list[int]:
    spectrum = _normalize_rgb(spectrum_rgb, _spectrum_rgb_from_theme(theme_rgb))
    return _contrast_rgb_from_spectrum(spectrum)


def _augment_cover_palette(palette: dict | None = None) -> dict:
    provided = palette if isinstance(palette, dict) else {}
    base = dict(_DEFAULT_COVER_PALETTE)
    base.update(provided)
    theme = _normalize_rgb(
        provided.get("cover_theme_rgb") or provided.get("cover_palette_rgb") or base.get("cover_theme_rgb"),
        _DEFAULT_COVER_PALETTE["cover_theme_rgb"],
    )
    spectrum_source = provided.get("spectrum_rgb") if "spectrum_rgb" in provided else None
    spectrum = _normalize_rgb(spectrum_source, _spectrum_rgb_from_theme(theme))
    inverse_source = None
    if "cover_inverse_rgb" in provided:
        inverse_source = provided.get("cover_inverse_rgb")
    elif "spectrum_block_rgb" in provided:
        inverse_source = provided.get("spectrum_block_rgb")
    inverse = _normalize_rgb(inverse_source, _inverse_rgb_from_theme(theme, spectrum))
    block_source = provided.get("spectrum_block_rgb") if "spectrum_block_rgb" in provided else None
    block = _normalize_rgb(block_source, inverse)
    return {
        "cover_palette_rgb": _normalize_rgb(base.get("cover_palette_rgb"), theme),
        "cover_palette_1": _normalize_rgb(base.get("cover_palette_1"), theme),
        "cover_palette_2": _normalize_rgb(base.get("cover_palette_2"), theme),
        "cover_palette_3": _normalize_rgb(base.get("cover_palette_3"), theme),
        "cover_theme_rgb": theme,
        "cover_inverse_rgb": inverse,
        "spectrum_rgb": spectrum,
        "spectrum_block_rgb": block,
    }


def _clone_cover_palette(palette: dict | None = None) -> dict:
    return _augment_cover_palette(palette)


def _load_pillow_modules():
    global _pillow_missing_logged
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
        return Image, ImageEnhance, ImageFilter, ImageOps
    except Exception as exc:
        if not _pillow_missing_logged:
            logger.warning("[media] Pillow not available; cover ambient image falls back to original cover: %s", exc)
            _pillow_missing_logged = True
        return None, None, None, None


def _bucket_channel(value: int) -> int:
    return max(0, min(255, int(round(value / 24) * 24)))


def _palette_payload(raw_colors: list[tuple[int, int, int]]) -> dict:
    if not raw_colors:
        return _clone_cover_palette()
    c1 = raw_colors[0]
    c2 = raw_colors[1] if len(raw_colors) > 1 else c1
    c3 = raw_colors[2] if len(raw_colors) > 2 else c2
    # Match the former front-end tone mapping: responsive to album art, but
    # cooled/desaturated so one cover cannot flood the whole stage.
    themed = [
        round(c1[0] * 0.42 + 184 * 0.58),
        round(c1[1] * 0.42 + 198 * 0.58),
        round(c1[2] * 0.42 + 235 * 0.58),
    ]
    spectrum = _spectrum_rgb_from_theme(themed)
    return _augment_cover_palette({
        "cover_palette_rgb": themed,
        "cover_palette_1": list(c1),
        "cover_palette_2": list(c2),
        "cover_palette_3": list(c3),
        "spectrum_rgb": spectrum,
        "spectrum_block_rgb": _contrast_rgb_from_spectrum(spectrum),
    })


def _extract_palette_from_image(img) -> dict:
    Image, _, _, _ = _load_pillow_modules()
    if Image is None:
        return _clone_cover_palette()
    try:
        resample = getattr(getattr(Image, "Resampling", Image), "BILINEAR", Image.BILINEAR)
        sample = img.convert("RGBA")
        sample.thumbnail((48, 48), resample)
        buckets: dict[tuple[int, int, int], float] = {}
        chroma_buckets: dict[tuple[int, int, int], float] = {}
        for r, g, b, a in sample.getdata():
            if a < 200:
                continue
            hi = max(r, g, b)
            lo = min(r, g, b)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if hi < 30 or lo > 235 or lum < 22 or lum > 244:
                continue
            key = (_bucket_channel(r), _bucket_channel(g), _bucket_channel(b))
            saturation = (hi - lo) / max(hi, 1)
            mid_luma_bonus = max(0.0, 1.0 - abs(lum - 142) / 142)
            # Count still matters, but do not let large black/white/grey areas
            # drown the small saturated cover accent that users notice as a theme.
            neutral_weight = 0.22 + saturation * 1.35 + mid_luma_bonus * 0.18
            buckets[key] = buckets.get(key, 0.0) + neutral_weight
            if saturation >= 0.16 and 34 <= lum <= 232:
                chroma_weight = 0.2 + saturation * saturation * 7.0 + mid_luma_bonus * 0.45
                chroma_buckets[key] = chroma_buckets.get(key, 0.0) + chroma_weight
        primary = chroma_buckets if chroma_buckets else buckets
        ranked = sorted(primary.items(), key=lambda item: item[1], reverse=True)
        if len(ranked) < 3 and primary is not buckets:
            seen = {color for color, _ in ranked}
            ranked.extend(item for item in sorted(buckets.items(), key=lambda item: item[1], reverse=True) if item[0] not in seen)
        return _palette_payload([color for color, _ in ranked[:3]])
    except Exception as exc:
        logger.debug("[media] cover palette extraction failed: %s", exc)
        return _clone_cover_palette()


def _build_ambient_cover(data: bytes, mime: str) -> tuple[bytes, str, dict]:
    Image, ImageEnhance, ImageFilter, ImageOps = _load_pillow_modules()
    if Image is None:
        return data, mime or "image/jpeg", _clone_cover_palette()
    try:
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
        with Image.open(io.BytesIO(data)) as src:
            img = ImageOps.exif_transpose(src)
            palette = _extract_palette_from_image(img)
            ambient = img.convert("RGB")
        ambient.thumbnail((240, 240), resample)
        # Heavy blur — generalized ambient wash, no recognizable edges.
        ambient = ambient.filter(ImageFilter.GaussianBlur(radius=10))
        ambient = ImageEnhance.Color(ambient).enhance(1.2)
        ambient = ImageEnhance.Contrast(ambient).enhance(1.1)
        ambient = ImageEnhance.Brightness(ambient).enhance(1.1)
        out = io.BytesIO()
        ambient.save(out, format="JPEG", quality=82, progressive=False)
        return out.getvalue(), "image/jpeg", palette
    except Exception as exc:
        logger.debug("[media] cover ambient generation failed: %s", exc)
        return data, mime or "image/jpeg", _clone_cover_palette()


def _ensure_cover_art_cache(identity: str | None = None) -> dict:
    """Generate ambient art + palette once per cover identity and return cache snapshot."""
    target = identity or _current_cover_identity()
    if not target:
        return {
            "identity": "",
            "ambient": b"",
            "ambient_mime": "image/jpeg",
            "palette": _clone_cover_palette(),
        }
    with _cover_art_lock:
        if _cover_art_cache.get("identity") == target:
            return {
                "identity": str(_cover_art_cache.get("identity") or ""),
                "ambient": bytes(_cover_art_cache.get("ambient") or b""),
                "ambient_mime": str(_cover_art_cache.get("ambient_mime") or "image/jpeg"),
                "palette": _clone_cover_palette(_cover_art_cache.get("palette")),
            }

    data, mime = get_cover_bytes()
    if not data:
        return {
            "identity": "",
            "ambient": b"",
            "ambient_mime": mime or "image/jpeg",
            "palette": _clone_cover_palette(),
        }
    ambient, ambient_mime, palette = _build_ambient_cover(data, mime)
    palette = _clone_cover_palette(palette)
    with _cover_art_lock:
        _cover_art_cache.update({
            "identity": target,
            "ambient": ambient,
            "ambient_mime": ambient_mime or "image/jpeg",
            "palette": palette,
        })
    return {
        "identity": target,
        "ambient": ambient,
        "ambient_mime": ambient_mime or "image/jpeg",
        "palette": _clone_cover_palette(palette),
    }


def get_cover_palette(identity: str | None = None, ensure: bool = False) -> dict:
    """Return backend cover colors; optionally generate them before replying."""
    target = identity or _current_cover_identity()
    if ensure:
        cached = _ensure_cover_art_cache(target)
        if cached.get("identity") == target:
            return _clone_cover_palette(cached.get("palette"))
    with _cover_art_lock:
        if target and _cover_art_cache.get("identity") == target:
            return _clone_cover_palette(_cover_art_cache.get("palette"))
    return _clone_cover_palette()


def get_cover_ambient_bytes() -> tuple[bytes | None, str]:
    """Return cached backend-blurred cover art, generating it once per identity."""
    identity = _current_cover_identity()
    if not identity:
        return None, "image/jpeg"
    cached = _ensure_cover_art_cache(identity)
    if cached.get("identity") == identity and cached.get("ambient"):
        return bytes(cached["ambient"]), str(cached.get("ambient_mime") or "image/jpeg")
    return None, str(cached.get("ambient_mime") or "image/jpeg")


def _fetch_ypm_direct() -> dict | None:
    """主进程直连 YesPlayMusic，作为 SMTC worker 挂掉时的硬兜底。"""
    now = time.time()
    if not _ypm_cache["ok"] and (now - float(_ypm_cache["ts"] or 0)) < 2:
        return None
    try:
        r = _requests.get(_YPM_API, timeout=0.8)
        data = r.json()
        track = data.get("currentTrack") or {}
        progress = data.get("progress")
        duration_ms = track.get("dt")
        if progress is None or not duration_ms or not track.get("name"):
            _ypm_cache.update({"ok": False, "ts": now, "data": None})
            return None
        artists = " / ".join(a.get("name", "") for a in (track.get("ar") or []))
        al = track.get("al") or {}
        album = al.get("name") if isinstance(al, dict) else ""
        cover_url = al.get("picUrl") if isinstance(al, dict) else ""
        payload = {
            "status": "playing",
            "title": track.get("name") or "",
            "artist": artists,
            "album": album or "",
            "position": float(progress),
            "duration": float(duration_ms) / 1000.0,
            "progress_ratio": float(progress) / (float(duration_ms) / 1000.0),
            "song_id": track.get("id"),
            "cover_url": cover_url or "",
            "position_source": "api",
        }
        _ypm_cache.update({"ok": True, "ts": now, "data": payload})
        return payload
    except Exception:
        _ypm_cache.update({"ok": False, "ts": now, "data": None})
        return None


def _terminate_smtc_process(proc) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=0.5)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass



def _smtc_reader_loop():
    global _smtc_result, _smtc_last_update
    global _smtc_started, _smtc_thread, _smtc_process
    try:
        while not _smtc_stop_event.is_set():
            proc = None
            try:
                proc = popen_hidden(
                    [_SMTC_PYTHON, _SMTC_WORKER],
                    stdout=_media_sp.PIPE,
                    stderr=_media_sp.DEVNULL,
                    bufsize=0,
                )
                with _smtc_lifecycle_lock:
                    _smtc_process = proc
                    stopping = _smtc_stop_event.is_set()
                if stopping:
                    _terminate_smtc_process(proc)
                    break
                assert proc.stdout is not None
                # binary line reader (bufsize=0); join chunks until \n
                buf = b""
                while not _smtc_stop_event.is_set():
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        raw_line, buf = buf.split(b"\n", 1)
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            info = json.loads(line)
                            _update_cover_from_worker(info)
                            with _smtc_lock:
                                _smtc_result = info
                                _smtc_last_update = time.time()
                        except json.JSONDecodeError:
                            continue
                if not _smtc_stop_event.is_set():
                    proc.wait(timeout=1)
            except Exception as e:
                if not _smtc_stop_event.is_set():
                    logger.error(f"[media] worker error: {e}")
            finally:
                _terminate_smtc_process(proc)
                with _smtc_lifecycle_lock:
                    if _smtc_process is proc:
                        _smtc_process = None
            # Interruptible wait prevents the old 2-second crash delay from
            # spawning another worker after service shutdown.
            if _smtc_stop_event.wait(2):
                break
    finally:
        with _smtc_lifecycle_lock:
            if _smtc_thread is threading.current_thread():
                _smtc_thread = None
                _smtc_started = False
            _smtc_process = None



def _ensure_smtc_thread():
    global _smtc_started, _smtc_thread
    with _smtc_lifecycle_lock:
        if _smtc_thread and _smtc_thread.is_alive():
            return
        _smtc_stop_event.clear()
        _smtc_started = True
        _smtc_thread = threading.Thread(
            target=_smtc_reader_loop,
            daemon=True,
            name="media-smtc-reader",
        )
        _smtc_thread.start()



def stop_media_service(timeout: float = 5) -> None:
    """Stop the SMTC reader and its child process; lazy access may restart it."""
    global _smtc_started, _smtc_thread, _smtc_process
    _smtc_stop_event.set()
    with _smtc_lifecycle_lock:
        thread = _smtc_thread
        proc = _smtc_process
    _terminate_smtc_process(proc)
    if thread is not None and thread is not threading.current_thread():
        thread.join(max(0.0, float(timeout)))
    with _smtc_lifecycle_lock:
        if _smtc_thread is thread and (thread is None or not thread.is_alive()):
            _smtc_thread = None
            _smtc_started = False
        if _smtc_process is proc:
            _smtc_process = None




# ============================================================
# LRC Parsing
# ============================================================


def _parse_lrc(lrc_text: str) -> list:
    """解析 LRC 歌词为 [(seconds, text), ...]"""
    result = []
    for line in lrc_text.split("\n"):
        matches = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        text = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", line).strip()
        if not text or not matches:
            continue
        for m, s in matches:
            sec = int(m) * 60 + float(s)
            result.append((sec, text))
    result.sort(key=lambda x: x[0])
    return result


# ============================================================
# Matching Helpers
# ============================================================

_LYRIC_JUNK_KW = (
    "DJ", "remix", "Remix", "REMIX", "翻自", "原唱", "钢琴", "伴奏",
    "伤感版", "女声", "男声", "Cover", "cover", "Acoustic",
    "Live版", "live版", "Live)", "Live）", "正式版",
    " beat", " Beat", " BEAT", "Type Beat",
)
_LYRIC_SCORE_THRESHOLD = 25
_FAKE_ARTIST_SUFFIX_CHARS = "-.·、_~—,、."


def _strip_paren(s: str) -> str:
    """去掉歌名尾部的括号后缀"""
    s = (s or "").strip().lower()
    for ch in ("(", "（"):
        i = s.find(ch)
        if i > 0:
            s = s[:i].strip()
    return s


def _is_fake_artist_variant(candidate: str, target: str) -> bool:
    if not candidate or not target:
        return False
    stripped = candidate.rstrip(_FAKE_ARTIST_SUFFIX_CHARS).strip()
    return stripped == target and stripped != candidate


def _has_extra_junk(candidate: str, target: str) -> bool:
    for kw in _LYRIC_JUNK_KW:
        if kw in candidate and kw not in target:
            return True
    return False


def _score_candidates(songs: list, title: str, artist: str) -> list:
    """对候选歌曲列表打分，返回 [(score, song_id, duration_sec), ...] 按分数降序。"""
    target_title = _strip_paren(title)
    target_artist = (artist or "").strip().lower()

    candidates = []
    for s in songs:
        name_full = s.get("name", "") or ""
        if _strip_paren(name_full) != target_title:
            continue

        artists = [(a or "").strip().lower() for a in s.get("artists", [])]
        artist_score = 0
        for a in artists:
            if a == target_artist:
                artist_score = 3
                break
            if _is_fake_artist_variant(a, target_artist):
                continue
            if target_artist and (target_artist in a or a in target_artist):
                artist_score = max(artist_score, 2)

        name_score = 2 if name_full.strip().lower() == (title or "").strip().lower() else 1
        junk_pen = -6 if _has_extra_junk(name_full, title or "") else 0
        total = artist_score * 10 + name_score * 3 + junk_pen

        if total >= _LYRIC_SCORE_THRESHOLD:
            candidates.append((total, s["id"], s.get("duration_sec", 0.0)))

    candidates.sort(key=lambda x: -x[0])
    return candidates


# ============================================================
# Netease Music (Public API, no third-party service)
# ============================================================


def _search_netease(title: str, artist: str) -> list:
    """搜索网易云公开接口，返回统一候选列表。"""
    kw = f"{title} {artist}"
    try:
        resp = _requests.get(
            "https://music.163.com/api/search/get",
            params={"s": kw, "type": 1, "limit": 30, "offset": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json()
        songs = (data.get("result") or {}).get("songs", []) or []
        return [
            {
                "id": s.get("id"),
                "name": s.get("name", "") or "",
                "artists": [(a.get("name", "") or "") for a in (s.get("artists") or [])],
                "duration_sec": (s.get("duration") or 0) / 1000.0,
            }
            for s in songs if s.get("id")
        ]
    except Exception as e:
        logger.error(f"[media] netease search error: {e}")
        return []


def _fetch_netease_lyrics(song_id: int) -> str:
    """从网易云公开接口获取 LRC 歌词文本。"""
    try:
        resp = _requests.get(
            "http://music.163.com/api/song/lyric",
            params={"id": song_id, "lv": -1, "kv": -1, "tv": -1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json()
        return (data.get("lrc") or {}).get("lyric", "") or ""
    except Exception as e:
        logger.error(f"[media] netease lyric error: {e}")
        return ""


# ============================================================
# QQ Music (Public API)
# ============================================================


def _search_qq_music(title: str, artist: str) -> list:
    """搜索 QQ 音乐公开接口，返回统一候选列表。"""
    kw = f"{title} {artist}"
    try:
        resp = _requests.get(
            "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
            params={"w": kw, "format": "json", "p": 1, "n": 20, "cr": 1},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://y.qq.com"},
            timeout=5,
        )
        data = resp.json()
        songs = (data.get("data") or {}).get("song") or {}
        song_list = songs.get("list") or []
        return [
            {
                "id": s.get("songmid", ""),
                "name": s.get("songname", "") or "",
                "artists": [(singer.get("name", "") or "") for singer in (s.get("singer") or [])],
                "duration_sec": s.get("interval") or 0,
            }
            for s in song_list if s.get("songmid")
        ]
    except Exception as e:
        logger.error(f"[media] qq music search error: {e}")
        return []


def _fetch_qq_lyrics(songmid: str) -> str:
    """从 QQ 音乐公开接口获取 LRC 歌词文本（base64 编码）。"""
    try:
        resp = _requests.get(
            "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
            params={"songmid": songmid, "format": "json", "nobase64": 0},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://y.qq.com",
            },
            timeout=5,
        )
        data = resp.json()
        lyric_b64 = data.get("lyric", "")
        if lyric_b64:
            return base64.b64decode(lyric_b64).decode("utf-8", errors="replace")
        return ""
    except Exception as e:
        logger.error(f"[media] qq music lyric error: {e}")
        return ""


# ============================================================
# Lyrics Search Orchestration (Netease → QQ Music fallback)
# ============================================================

_lyrics_cache = {}
_lyrics_cache_lock = threading.Lock()
_LYRICS_CACHE_MAX = 20
_lyrics_cache_order = []


def _load_lyrics_by_netease_id(song_id: int, duration_sec: float) -> dict:
    """按网易云 song_id 拉取并解析歌词。"""
    lrc_text = _fetch_netease_lyrics(song_id)
    lyrics = _parse_lrc(lrc_text) if lrc_text else []
    return {"song_id": song_id, "duration": duration_sec, "lyrics": lyrics, "manual": False}


def _search_and_fetch(title: str, artist: str) -> dict | None:
    """双源搜索：网易云优先，QQ 音乐 fallback。返回结果 dict 或 None。"""

    # 1. 网易云搜索
    netease_songs = _search_netease(title, artist)
    candidates = _score_candidates(netease_songs, title, artist)
    for score, sid, dur in candidates[:3]:
        r = _load_lyrics_by_netease_id(sid, dur)
        if r["lyrics"]:
            return r

    # 2. QQ 音乐 fallback
    qq_songs = _search_qq_music(title, artist)
    qq_candidates = _score_candidates(qq_songs, title, artist)
    for score, songmid, dur in qq_candidates[:3]:
        lrc_text = _fetch_qq_lyrics(songmid)
        lyrics = _parse_lrc(lrc_text) if lrc_text else []
        if lyrics:
            return {"song_id": songmid, "duration": dur, "lyrics": lyrics, "manual": False}

    if not candidates and not qq_candidates:
        logger.warning(f"[media] no match on any source: {title} - {artist}")
    return None


def _get_lyrics_for(title: str, artist: str, song_id: int = 0) -> dict:
    """获取歌词（带缓存）。song_id 可从 YesPlayMusic API 直接提供。"""
    key = (title, artist)

    with _lyrics_cache_lock:
        cached = _lyrics_cache.get(key)
    if cached is not None:
        return cached

    result = None

    if song_id:
        r = _load_lyrics_by_netease_id(song_id, 0.0)
        if r["lyrics"]:
            result = r

    if not result:
        result = _search_and_fetch(title, artist)

    if not result:
        return {"song_id": None, "duration": 0.0, "lyrics": [], "manual": False}

    if result["lyrics"]:
        with _lyrics_cache_lock:
            _lyrics_cache[key] = result
            _lyrics_cache_order.append(key)
            while len(_lyrics_cache_order) > _LYRICS_CACHE_MAX:
                old_key = _lyrics_cache_order.pop(0)
                _lyrics_cache.pop(old_key, None)
        logger.info(f"[media] loaded lyrics for: {title} "
                    f"(lrc={len(result['lyrics'])} lines, duration={result['duration']:.1f}s)")

    return result


# ============================================================
# Public API
# ============================================================


def get_media_info() -> dict:
    """获取完整媒体信息 + 当前歌词。

    优先读 SMTC worker 结果；若 worker 卡住/空闲，则直连 YesPlayMusic 兜底。
    """
    _ensure_smtc_thread()
    with _smtc_lock:
        info = dict(_smtc_result)
        last = _smtc_last_update

    stale = (not last) or (time.time() - last > 3.0)
    ypm = None
    if info.get("status") not in ("playing", "paused") or not info.get("title") or stale:
        ypm = _fetch_ypm_direct()
        if ypm:
            info = dict(ypm)
            if ypm.get("cover_url"):
                with _cover_lock:
                    _set_cover_locked(
                        key=_cover_key(ypm["title"], ypm.get("artist", ""), ypm.get("album", "")),
                        title=ypm["title"],
                        artist=ypm.get("artist", ""),
                        source_token=f"url:{ypm['cover_url']}",
                        url=ypm["cover_url"],
                        clear_other=True,
                    )

    if info.get("status") not in ("playing", "paused") or not info.get("title"):
        offset = _load_lyric_offset()
        palette = _clone_cover_palette()
        return {
            "status": info.get("status") or "idle", "title": "", "artist": "", "album": "",
            "lyric": "", "next_lyric": "",
            "lyrics": [], "lyrics_yrc": [], "song_id": None,
            "position": 0, "duration": 0, "progress_ratio": None, "position_source": "none",
            "position_effective": 0,
            "lyric_offset": offset,
            "lyric_index": -1,
            "next_lyric_index": -1,
            "lyric_scroll": 0.0,
            "lyric_line_progress": 0.0,
            "server_ts": time.time(),
            "has_cover": False, "cover_url": "", "cover_version": 0, "cover_identity": "",
            **palette,
        }

    # refresh file cover if worker finished async extract
    _load_cover_file_if_any(info.get("title", ""), info.get("artist", ""), info.get("album", ""))

    lyric_data = _get_lyrics_for(info["title"], info["artist"],
                                  song_id=info.get("song_id") or 0)
    lyrics = lyric_data["lyrics"]
    duration = lyric_data["duration"]
    song_id = lyric_data.get("song_id")

    ratio = info.get("progress_ratio")
    position_source = "none"
    pos = 0.0

    if info.get("position") is not None and info.get("duration"):
        pos = float(info["position"])
        duration = float(info["duration"])
        position_source = info.get("position_source") or "api"
    elif ratio is not None and duration > 0:
        pos = ratio * duration
        position_source = "uia"

    lyric_offset = _load_lyric_offset()
    pos_eff = pos + lyric_offset
    lyric_index = -1
    next_lyric_index = -1
    current_lyric = ""
    next_lyric = ""
    lyric_scroll = 0.0
    lyric_line_progress = 0.0
    lyric_start = 0.0
    lyric_end = 0.0
    lyric_duration = 0.0
    lyric_elapsed = 0.0
    if lyrics:
        for i, (t, text) in enumerate(lyrics):
            if float(t) <= pos_eff:
                lyric_index = i
                current_lyric = text
            else:
                break
        if lyric_index < 0 and lyrics:
            # Before the first timed line: keep index -1 so clients can show title/artist fallback.
            next_lyric_index = 0
            next_lyric = lyrics[0][1]
            try:
                lyric_end = float(lyrics[0][0] or 0.0)
            except (TypeError, ValueError, IndexError):
                lyric_end = 0.0
            lyric_start = max(0.0, lyric_end - 1.0)
            lyric_duration = max(0.18, lyric_end - lyric_start)
            lyric_elapsed = max(0.0, min(lyric_duration, pos_eff - lyric_start))
        elif lyric_index >= 0:
            if lyric_index + 1 < len(lyrics):
                next_lyric_index = lyric_index + 1
                next_lyric = lyrics[next_lyric_index][1]
            else:
                next_lyric_index = -1
                next_lyric = ""
            lyric_start, lyric_end, lyric_duration = _lyric_line_window(lyrics, lyric_index)
            lyric_elapsed = max(0.0, min(lyric_duration, pos_eff - lyric_start))
            lyric_scroll, lyric_line_progress = _lyric_progress_pair(
                lyric_start, lyric_duration, pos_eff
            )

    cover = get_cover_state()
    cover_key = _cover_key(info["title"], info.get("artist", ""), info.get("album", ""))
    same_track = (not cover.get("key")) or cover.get("key") == cover_key or cover.get("title") == info["title"]
    has_file = bool(cover.get("b64")) and same_track
    has_remote = bool(cover.get("url")) and same_track
    has_cover = has_file or has_remote
    # Millisecond precision makes the proxy URL unique even during quick skips.
    # It remains below JavaScript's safe integer limit.
    cover_version = int(float(cover.get("updated_at") or 0) * 1000)
    if has_file:
        cover_url = f"/api/media/cover?v={cover_version}"
    elif has_remote:
        # Use proxy endpoint so music page always same-origin (palette extraction works)
        cover_url = f"/api/media/cover?v={cover_version}"
    else:
        cover_url = ""
    cover_identity = ""
    if has_remote:
        cover_identity = f"url:{cover.get('url') or ''}"
    elif has_file:
        cover_identity = str(cover.get("source_token") or cover_key)

    # Generate palette in the media payload itself so /music can paint spectrum
    # colors immediately; the ambient endpoint reuses the same cached result.
    palette = get_cover_palette(cover_identity, ensure=has_cover)

    return {
        "status": info["status"],
        "title": info["title"],
        "artist": info.get("artist") or "",
        "album": info.get("album") or "",
        "lyric": current_lyric,
        "next_lyric": next_lyric,
        "lyrics": [[t, text] for t, text in lyrics],
        "lyrics_yrc": [],  # YRC removed, keep field for frontend compat
        "song_id": song_id,
        "position": round(pos, 2),
        "position_effective": round(pos_eff, 2),
        "duration": round(duration, 2),
        "progress_ratio": ratio,
        "position_source": position_source,
        "lyric_offset": round(float(lyric_offset), 1),
        "lyric_index": lyric_index,
        "next_lyric_index": next_lyric_index,
        "lyric_start": round(lyric_start, 3),
        "lyric_end": round(lyric_end, 3),
        "lyric_duration": round(lyric_duration, 3),
        "lyric_elapsed": round(lyric_elapsed, 3),
        "lyric_scroll": round(lyric_scroll, 4),
        "lyric_line_progress": round(lyric_line_progress, 4),
        "server_ts": time.time(),
        "has_cover": has_cover,
        "cover_url": cover_url,
        "cover_version": cover_version,
        "cover_identity": cover_identity,
        "cover_palette_rgb": palette.get("cover_palette_rgb"),
        "cover_palette_1": palette.get("cover_palette_1"),
        "cover_palette_2": palette.get("cover_palette_2"),
        "cover_palette_3": palette.get("cover_palette_3"),
        "cover_theme_rgb": palette.get("cover_theme_rgb"),
        "cover_inverse_rgb": palette.get("cover_inverse_rgb"),
        "spectrum_rgb": palette.get("spectrum_rgb"),
        "spectrum_block_rgb": palette.get("spectrum_block_rgb"),
    }


def _lyric_line_window(lyrics: list, index: int) -> tuple[float, float, float]:
    """Return (start, end, duration) for one LRC line in offset-effective seconds."""
    if index < 0 or index >= len(lyrics):
        return 0.0, 0.0, 0.0
    try:
        start = float(lyrics[index][0] or 0.0)
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0, 0.0
    if index + 1 < len(lyrics):
        try:
            end = float(lyrics[index + 1][0] or (start + 3.0))
        except (TypeError, ValueError, IndexError):
            end = start + 3.0
    else:
        end = start + 3.0
    if end <= start:
        end = start + 0.18
    return start, end, max(0.18, end - start)


def _lyric_progress_pair(start: float, duration: float, pos_eff: float) -> tuple[float, float]:
    """Return (scroll_progress, line_progress) from line timing + effective position."""
    span = max(0.18, float(duration or 0.0))
    elapsed = max(0.0, float(pos_eff) - float(start or 0.0))
    line_prog = max(0.0, min(1.0, elapsed / span))
    # Hold for the first third of the line (capped at 3s total scroll window), then scroll.
    scroll_duration = min(3.0, span)
    hold = scroll_duration / 3.0
    move_duration = max(0.12, scroll_duration - hold)
    scroll = max(0.0, min(1.0, (elapsed - hold) / move_duration))
    return scroll, line_prog


def get_lyric_frame() -> dict:
    """On-change lyric control frame: line text + how long that line lasts.

    Clients should switch sentences only when this frame arrives, and use
    ``lyric_duration`` / ``lyric_elapsed`` to locally animate long-line marquee.
    """
    full = get_media_info()
    return {
        "status": full.get("status") or "idle",
        "title": full.get("title") or "",
        "artist": full.get("artist") or "",
        "album": full.get("album") or "",
        "playing": (full.get("status") == "playing"),
        "song_id": full.get("song_id"),
        "lyric": full.get("lyric") or "",
        "next_lyric": full.get("next_lyric") or "",
        "lyric_index": int(full.get("lyric_index", -1) if full.get("lyric_index") is not None else -1),
        "next_lyric_index": int(full.get("next_lyric_index", -1) if full.get("next_lyric_index") is not None else -1),
        "lyric_offset": full.get("lyric_offset", 0),
        "lyric_start": full.get("lyric_start", 0.0),
        "lyric_end": full.get("lyric_end", 0.0),
        "lyric_duration": full.get("lyric_duration", 0.0),
        "lyric_elapsed": full.get("lyric_elapsed", 0.0),
        "lyric_scroll": full.get("lyric_scroll", 0.0),
        "lyric_line_progress": full.get("lyric_line_progress", 0.0),
        "position": full.get("position", 0),
        "position_effective": full.get("position_effective", 0),
        "duration": full.get("duration", 0),
        "position_source": full.get("position_source") or "none",
        "server_ts": full.get("server_ts") or time.time(),
        "track_key": "\u001f".join([
            "" if full.get("song_id") is None else str(full.get("song_id")),
            str(full.get("title") or ""),
            str(full.get("artist") or ""),
            str(full.get("album") or ""),
        ]),
    }


# ============================================================
# Lyric Offset
# ============================================================

_LYRIC_OFFSET_DEFAULT = 1.5


def _load_lyric_offset() -> float:
    from core.config import load_config as _lc
    return float(_lc().get("lyric_offset", _LYRIC_OFFSET_DEFAULT))


def _save_lyric_offset(val: float):
    from core.config import set_config_value
    set_config_value("lyric_offset", val)


# ============================================================
# Service-Level Functions (called by dashboard routes)
# ============================================================


def reload_current_media() -> dict:
    """Clear the current song lyric cache and return fresh media info."""
    with _smtc_lock:
        title = _smtc_result.get("title", "")
        artist = _smtc_result.get("artist", "")
    if title:
        key = (title, artist)
        with _lyrics_cache_lock:
            _lyrics_cache.pop(key, None)
            try:
                _lyrics_cache_order.remove(key)
            except ValueError:
                pass
        logger.info(f"[media] lyrics cache cleared for: {title}")
    return get_media_info()



def get_media_status() -> dict:
    """Return SMTC worker status without starting or contacting the worker."""
    with _smtc_lock:
        last_update = _smtc_last_update
        media_state = _smtc_result.get("status", "idle")
    age = time.time() - last_update if last_update else None
    stale = bool(_smtc_started and age is not None and age > 15)
    if not _smtc_started:
        status = "unknown"
    elif stale:
        status = "stale"
    elif last_update:
        status = "ok"
    else:
        status = "unknown"
    return {
        "status": status,
        "ok": status == "ok",
        "enabled": True,
        "stale": stale,
        "error": None,
        "last_success_at": last_update or None,
        "details": {"media_state": media_state, "worker_started": _smtc_started},
    }


def load_lyric_offset() -> float:
    return _load_lyric_offset()


def save_lyric_offset(val: float):
    _save_lyric_offset(val)


def normalize_lyric_offset(payload: dict) -> float:
    if "delta" in payload:
        return round((_load_lyric_offset() + float(payload["delta"])) * 10) / 10
    return round(float(payload.get("offset", _LYRIC_OFFSET_DEFAULT)) * 10) / 10
