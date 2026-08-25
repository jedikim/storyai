"""FastAPI REST and static-web entry point for the P4 storyai UI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from storyai import __version__

from .core.service import StoryService
from .runtime import get_service
from .ui_data import UIDataStore


class CommitRequest(BaseModel):
    proposal_id: str
    mode: Literal["apply", "dry_run"] = "apply"
    allow_cycles: bool = False
    max_iterations: int | None = None


class ProposalRequest(BaseModel):
    proposal_id: str


def create_ui_app(
    service: StoryService | None = None,
    *,
    dist_dir: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="storyai UI API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def current() -> StoryService:
        return service or get_service()

    def data() -> UIDataStore:
        return UIDataStore(current())

    @app.exception_handler(ValueError)
    def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/health")
    def health() -> dict:
        return data().status()

    @app.get("/api/graph")
    def graph(as_of: int | None = Query(default=None, ge=0)) -> dict:
        return data().graph(as_of=as_of)

    @app.get("/api/nodes/{ref:path}")
    def node(ref: str, as_of: int | None = Query(default=None, ge=0)) -> dict:
        return data().node(ref, as_of=as_of)

    @app.get("/api/search")
    def search(q: str = Query(min_length=1), as_of: int | None = Query(default=None, ge=0)):
        values = current().find(q, as_of=as_of, mode="hybrid", limit=20)
        return [{**item, "layer": current().ontology.kinds[item["kind"]].layer} for item in values]

    @app.get("/api/promises")
    def promises(as_of: int | None = Query(default=None, ge=0)) -> list[dict]:
        return data().promises(as_of=as_of)

    @app.get("/api/timeline")
    def timeline() -> dict:
        return data().timeline()

    @app.get("/api/proposals")
    def proposals() -> list[dict]:
        return data().proposals()

    @app.post("/api/proposals/commit")
    def commit(request: CommitRequest) -> dict:
        return current().commit(
            request.proposal_id,
            mode=request.mode,
            allow_cycles=request.allow_cycles,
            max_iterations=request.max_iterations,
        )

    @app.post("/api/proposals/impact")
    def proposal_impact(request: ProposalRequest) -> dict:
        proposal = data().proposals(request.proposal_id)[0]
        previews = []
        for operation in proposal["ops"]:
            if operation["verb"] == "ADD":
                previews.append(
                    {
                        "ref": operation["target"],
                        "affected": [],
                        "broken_rules": [],
                        "new_node": True,
                    }
                )
                continue
            field = operation["field"] or "$node"
            try:
                previews.append(
                    current().impact(
                        operation["target"],
                        change={"field": field, "to": operation["to"]},
                    )
                )
            except ValueError as exc:
                previews.append({"ref": operation["target"], "affected": [], "error": str(exc)})
        return {"proposal_id": request.proposal_id, "previews": previews}

    static_root = Path(dist_dir).resolve() if dist_dir else Path(__file__).parent / "static"
    if static_root.is_dir() and (static_root / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_root, html=True), name="web")
    else:

        @app.get("/")
        def development_hint() -> dict[str, str]:
            return {
                "service": "storyai UI API",
                "frontend": (
                    "정적 UI가 없습니다. web에서 npm run build 또는 npm run dev를 실행하세요."
                ),
            }

    return app


app = create_ui_app()


def main() -> None:
    uvicorn.run(
        "server.ui:app",
        host=os.environ.get("STORYAI_UI_HOST", "127.0.0.1"),
        port=int(os.environ.get("STORYAI_UI_PORT", "8765")),
        reload=False,
    )


if __name__ == "__main__":
    main()
