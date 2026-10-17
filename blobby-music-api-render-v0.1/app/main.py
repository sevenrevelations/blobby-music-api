from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

APP_NAME = "Blobby Music API"
DEFAULT_ORIGINS = [
    "https://sevenrevelations.github.io",
    "http://localhost",
    "http://127.0.0.1",
]

extra_origins = [
    x.strip()
    for x in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if x.strip()
]
ALLOWED_ORIGINS = list(dict.fromkeys(DEFAULT_ORIGINS + extra_origins))

app = FastAPI(title=APP_NAME, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Lightweight best-effort in-memory protection against accidental abuse.
# This is per Render instance and is not a replacement for stronger auth later.
RATE_WINDOW_SECONDS = 60
RATE_LIMIT = 60
_hits: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)

    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    q = _hits[client]

    while q and now - q[0] > RATE_WINDOW_SECONDS:
        q.popleft()

    if len(q) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")

    q.append(now)
    return await call_next(request)


SEARCH_PROVIDERS = {
    "youtube": "ytsearch",
    "youtube_music": "ytsearch",
    "soundcloud": "scsearch",
}

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "music.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "soundcloud.com",
    "www.soundcloud.com",
    "m.soundcloud.com",
}


def _base_ydl_opts() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "ignoreconfig": True,
        "socket_timeout": 15,
        "retries": 1,
        "fragment_retries": 1,
        "cachedir": False,
    }


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _thumbnail(info: dict[str, Any]) -> str | None:
    direct = _safe_text(info.get("thumbnail"))
    if direct:
        return direct

    thumbs = info.get("thumbnails") or []
    for item in reversed(thumbs):
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return None


def _artist(info: dict[str, Any]) -> str:
    return (
        _safe_text(info.get("artist"))
        or _safe_text(info.get("uploader"))
        or _safe_text(info.get("channel"))
        or "Unknown artist"
    )


def _canonical_search_url(info: dict[str, Any], provider: str) -> str:
    webpage_url = _safe_text(info.get("webpage_url"))
    if webpage_url.startswith(("http://", "https://")):
        return webpage_url

    raw = _safe_text(info.get("url"))
    if raw.startswith(("http://", "https://")):
        return raw

    ident = _safe_text(info.get("id"))
    if provider in {"youtube", "youtube_music"} and ident:
        return f"https://www.youtube.com/watch?v={ident}"

    return ""


def _search_sync(query: str, provider: str, limit: int) -> list[dict[str, Any]]:
    prefix = SEARCH_PROVIDERS[provider]
    opts = _base_ydl_opts()
    opts.update({
        "extract_flat": "in_playlist",
        "playlistend": limit,
    })

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{prefix}{limit}:{query}", download=False)

    entries = (info or {}).get("entries") or []
    results = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        url = _canonical_search_url(entry, provider)
        if not url:
            continue

        results.append({
            "id": _safe_text(entry.get("id")),
            "provider": provider,
            "title": _safe_text(entry.get("title")) or "Untitled",
            "artist": _artist(entry),
            "duration": entry.get("duration"),
            "thumbnail": _thumbnail(entry),
            "url": url,
        })

    return results


def _validate_media_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid URL.") from exc

    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise HTTPException(
            status_code=400,
            detail="Only public YouTube/YouTube Music and SoundCloud URLs are supported.",
        )
    return url.strip()


def _provider_from_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "soundcloud.com" in host:
        return "soundcloud"
    if "youtube.com" in host or host == "youtu.be":
        return "youtube"
    return "unknown"


def _resolve_sync(url: str) -> dict[str, Any]:
    opts = _base_ydl_opts()
    opts.update({
        "format": "bestaudio/best",
        "extract_flat": False,
    })

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise RuntimeError("No media information returned.")

    # yt-dlp normally places the selected format URL here when a format is selected.
    stream_url = _safe_text(info.get("url"))
    selected_format = info

    # Fallback for extractors that return formats but no selected top-level URL.
    if not stream_url:
        formats = [
            f for f in (info.get("formats") or [])
            if isinstance(f, dict)
            and f.get("url")
            and f.get("acodec") not in (None, "none")
        ]
        audio_only = [f for f in formats if f.get("vcodec") in (None, "none")]
        pool = audio_only or formats

        if pool:
            def score(f: dict[str, Any]):
                return (
                    float(f.get("abr") or 0),
                    float(f.get("tbr") or 0),
                    float(f.get("asr") or 0),
                )
            selected_format = max(pool, key=score)
            stream_url = _safe_text(selected_format.get("url"))

    if not stream_url:
        raise RuntimeError("No playable public audio stream was returned.")

    headers = info.get("http_headers") or selected_format.get("http_headers") or {}

    return {
        "id": _safe_text(info.get("id")),
        "provider": _provider_from_host(url),
        "title": _safe_text(info.get("title")) or "Untitled",
        "artist": _artist(info),
        "duration": info.get("duration"),
        "thumbnail": _thumbnail(info),
        "page_url": _safe_text(info.get("webpage_url")) or url,
        "stream_url": stream_url,
        "ext": _safe_text(selected_format.get("ext") or info.get("ext")),
        "protocol": _safe_text(selected_format.get("protocol") or info.get("protocol")),
        "expires_note": "Resolved media URLs are temporary. Resolve again when playback expires.",
        "http_headers": {
            str(k): str(v)
            for k, v in headers.items()
            if str(k).lower() in {"user-agent", "referer", "origin"}
        },
    }


@app.get("/")
async def root():
    return {
        "name": APP_NAME,
        "version": "0.1.0",
        "providers": ["youtube", "youtube_music", "soundcloud"],
        "endpoints": ["/health", "/providers", "/search", "/resolve"],
    }


@app.get("/health")
async def health():
    return {"ok": True, "service": APP_NAME}


@app.get("/providers")
async def providers():
    return {
        "search": [
            {"id": "youtube", "name": "YouTube"},
            {"id": "youtube_music", "name": "YouTube Music"},
            {"id": "soundcloud", "name": "SoundCloud"},
        ],
        "resolve": ["youtube.com", "music.youtube.com", "youtu.be", "soundcloud.com"],
        "tidal": {
            "enabled": False,
            "reason": "No DRM/subscription bypass. TIDAL can be added later through supported metadata/account integrations.",
        },
    }


@app.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=150),
    provider: str = Query("youtube"),
    limit: int = Query(10, ge=1, le=20),
):
    provider = provider.strip().lower()
    if provider not in SEARCH_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported provider.")

    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_search_sync, q.strip(), provider, limit),
            timeout=25,
        )
        return {"query": q.strip(), "provider": provider, "results": results}
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Search timed out.") from exc
    except DownloadError as exc:
        raise HTTPException(status_code=502, detail=f"Provider search failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search failed: {exc}") from exc


@app.get("/resolve")
async def resolve(
    url: str = Query(..., min_length=8, max_length=1200),
):
    safe_url = _validate_media_url(url)

    try:
        item = await asyncio.wait_for(
            asyncio.to_thread(_resolve_sync, safe_url),
            timeout=30,
        )
        return item
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Media resolution timed out.") from exc
    except DownloadError as exc:
        raise HTTPException(status_code=502, detail=f"Provider resolution failed: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Resolution failed: {exc}") from exc
