"""Small on-disk cache for mod pictures (icons, screenshots) shown in the desktop
mod browser. Only https images from the mod sites' own image hosts are fetched
(core.mod_browse.safe_img), each capped at 6 MB; the cache keeps the newest 400.
"""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

MAX_BYTES = 6 * 1024 * 1024
KEEP = 400


class ImageCache:
    def __init__(self, folder: str, workers: int = 6):
        self.folder = folder
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="img")
        self._lock = threading.Lock()
        self._pruned = False

    def path_for(self, url: str) -> str:
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            ext = ".img"
        return os.path.join(self.folder, hashlib.sha1(url.encode("utf-8")).hexdigest() + ext)

    def get(self, url: Optional[str], opener=None) -> Optional[str]:
        """Local file for url (downloading it if needed), or None."""
        from core.mod_browse import safe_img
        url = safe_img(url)
        if not url:
            return None
        path = self.path_for(url)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        try:
            os.makedirs(self.folder, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": "BaoBun-ControlPanel"})
            with (opener or urllib.request.urlopen)(req, timeout=15) as r:
                data = r.read(MAX_BYTES + 1)
            if not data or len(data) > MAX_BYTES:
                return None
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception:
            return None
        self._prune()
        return path

    def fetch(self, url: Optional[str], done: Callable[[Optional[str]], None]):
        """Download in the background; done(path_or_None) runs on the worker thread."""
        def work():
            p = self.get(url)
            try:
                done(p)
            except Exception:
                pass
        self._pool.submit(work)

    def _prune(self):
        with self._lock:
            if self._pruned:
                return
            self._pruned = True
        try:
            files = [os.path.join(self.folder, f) for f in os.listdir(self.folder)]
            files = [f for f in files if os.path.isfile(f)]
            files.sort(key=os.path.getmtime, reverse=True)
            for f in files[KEEP:]:
                try:
                    os.remove(f)
                except OSError:
                    pass
        except OSError:
            pass
