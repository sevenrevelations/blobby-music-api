# Blobby Music API — Render v0.1

Small FastAPI backend for the Blobby Music rebuild.

## What v0.1 does

- YouTube / YouTube Music search through yt-dlp's YouTube search extractor.
- SoundCloud search through yt-dlp's SoundCloud search extractor.
- Resolves public YouTube/YouTube Music/SoundCloud URLs to temporary playable media information.
- CORS is enabled for `https://sevenrevelations.github.io`.
- No cookies, Spotify, TIDAL stream extraction, DRM bypass, login bypass, or downloading endpoint.

## Endpoints

- `GET /health`
- `GET /providers`
- `GET /search?q=hello&provider=youtube&limit=10`
- `GET /search?q=hello&provider=soundcloud&limit=10`
- `GET /resolve?url=https://...`

## Render

Deploy this folder as a Docker web service.

`render.yaml` is included for convenience. If Render's current dashboard offers a different free/paid plan selection, choose the available web-service plan manually; the app itself does not depend on a specific plan name.

After deployment, first test:

`https://YOUR-RENDER-HOST/health`

Expected:

```json
{"ok":true,"service":"Blobby Music API"}
```

Then test a search:

`https://YOUR-RENDER-HOST/search?q=steve%20lacy&provider=youtube&limit=5`

## Notes

Resolved provider media URLs are temporary and should not be stored permanently. The Blobby frontend should call `/resolve` again when a URL expires.

This first backend build is intentionally isolated from the existing Blobby frontend. Do not replace any existing `erelease` files yet.
