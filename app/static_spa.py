"""Static file serving with SPA index.html fallback for client-side routes."""

from __future__ import annotations

import os

import anyio
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_ASSET_EXTENSIONS = {
    ".js",
    ".css",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".json",
    ".webmanifest",
    ".txt",
}


def _looks_like_asset(path: str) -> bool:
    basename = path.rsplit("/", 1)[-1]
    if not basename or basename == "index.html":
        return False
    _, ext = os.path.splitext(basename)
    return ext.lower() in _ASSET_EXTENSIONS


def _should_spa_fallback(path: str) -> bool:
    norm = path.strip("/")
    if norm == "api" or norm.startswith("api/"):
        return False
    if _looks_like_asset(path):
        return False
    return True


class SPAStaticFiles(StaticFiles):
    """Serve static assets; unknown non-asset GET paths return index.html."""

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or scope["method"] not in ("GET", "HEAD"):
                raise
            if not self.html or not _should_spa_fallback(path):
                raise
            full_path, stat_result = await anyio.to_thread.run_sync(self.lookup_path, "index.html")
            if stat_result is None:
                raise
            return self.file_response(full_path, stat_result, scope)
