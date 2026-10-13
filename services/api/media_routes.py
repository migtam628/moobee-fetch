"""Authenticated inspection-only media endpoints for MooBee Fetch."""
import asyncio
import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from media import eligible, open_verified, MediaNotReady
from probe import inspect_fd

GID = re.compile(r"^[0-9a-f]{16}$")
FIELDS = ["gid", "status", "files", "bittorrent"]

class InspectionInput(BaseModel):
    gid: str = Field(min_length=16, max_length=16)
    index: int = Field(ge=1, le=512)


def build_router(rpc, root):
    router = APIRouter()

    @router.get("/api/media/candidates")
    async def candidates():
        """Only finished, present, selected files. No arbitrary path input."""
        active, waiting, stopped = await asyncio.gather(
            rpc("tellActive", [FIELDS]),
            rpc("tellWaiting", [0, 40, FIELDS]),
            rpc("tellStopped", [0, 100, FIELDS]),
        )
        items = []
        seen = set()
        for record in active + waiting + stopped:
            if not isinstance(record, dict) or record.get("status") != "complete":
                continue
            gid = record.get("gid")
            if not isinstance(gid, str) or not GID.fullmatch(gid) or gid in seen:
                continue
            seen.add(gid)
            for f in (record.get("files") or [])[:512]:
                try:
                    index = int(f["index"])
                    item = eligible(record, index, root())
                    fd = open_verified(item, root())
                    os.close(fd)
                except (KeyError, ValueError, TypeError, OSError, MediaNotReady):
                    continue
                items.append({"gid": gid, "index": index,
                              "name": item["name"], "size": item["size"],
                              "status": "needs_inspection"})
                if len(items) >= 60:
                    break
            if len(items) >= 60:
                break
        return {"items": items, "import_enabled": False,
                "note": "Preview only; MooBee Stream ingestion is not connected."}

    @router.post("/api/media/inspect")
    async def inspect(payload: InspectionInput):
        if not GID.fullmatch(payload.gid):
            raise HTTPException(422, "Invalid transfer ID")
        record = await rpc("tellStatus", [payload.gid, FIELDS])
        if not isinstance(record, dict) or record.get("gid") != payload.gid:
            raise HTTPException(409, "Transfer unavailable")
        try:
            item = eligible(record, payload.index, root())
            fd = open_verified(item, root())
            try:
                before = os.fstat(fd)
                video = await asyncio.to_thread(inspect_fd, fd)
                after = os.fstat(fd)
                keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
                if any(getattr(before, k) != getattr(after, k) for k in keys):
                    raise MediaNotReady("File changed during inspection")
            finally:
                os.close(fd)
        except (MediaNotReady, OSError) as exc:
            raise HTTPException(409, "Media not ready for inspection") from exc
        return {"gid": payload.gid, "index": payload.index,
                "name": item["name"], "size": item["size"],
                "video": video, "validated": True,
                "import_queued": False,
                "note": "Staging preflight complete; no media was imported."}

    return router
