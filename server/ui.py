"""FastAPI REST and static-web entry point for the P4 storyai UI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from storyai import __version__

from .core.service import StoryService
from .runtime import get_service, manage_project
from .ui_data import UIDataStore


class CommitRequest(BaseModel):
    proposal_id: str
    mode: Literal["apply", "dry_run"] = "apply"
    allow_cycles: bool = False
    max_iterations: int | None = None


class ProposalRequest(BaseModel):
    proposal_id: str


class ProjectSelectRequest(BaseModel):
    name: str


class NodeSummaryRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=8_000)
    rev: int = Field(ge=1)


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

    def project_list() -> dict:
        if service is None:
            return manage_project(mode="list")
        name = service.project_root.name
        return {
            "mode": "list",
            "selected": name,
            "projects": [
                {
                    "name": name,
                    "root": str(service.project_root),
                    "db": str(service.db_path),
                    "selected": True,
                    "available": True,
                }
            ],
        }

    def choose_project(name: str) -> dict:
        if service is None:
            return manage_project(mode="select", name=name)
        current_name = service.project_root.name
        if name != current_name:
            raise ValueError(f"고정된 UI 프로젝트는 전환할 수 없습니다: {current_name}")
        return {
            "mode": "select",
            "selected": current_name,
            "project": {
                "name": current_name,
                "root": str(service.project_root),
                "db": str(service.db_path),
                "selected": True,
                "available": True,
            },
        }

    @app.exception_handler(ValueError)
    def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/health")
    def health() -> dict:
        return data().status()

    @app.get("/api/projects")
    def projects() -> dict:
        result = project_list()
        return {
            "mode": "list",
            "selected": result["selected"],
            "projects": [
                {
                    "name": item["name"],
                    "selected": item["selected"],
                    "available": item["available"],
                }
                for item in result["projects"]
            ],
        }

    @app.post("/api/projects/select")
    def select_project(request: ProjectSelectRequest) -> dict:
        result = choose_project(request.name)
        project = result["project"]
        return {
            "mode": "select",
            "selected": result["selected"],
            "project": {
                "name": project["name"],
                "selected": project["selected"],
                "available": project["available"],
            },
        }

    @app.get("/api/graph")
    def graph(as_of: int | None = Query(default=None, ge=0)) -> dict:
        return data().graph(as_of=as_of)

    @app.get("/api/nodes/{ref:path}")
    def node(ref: str, as_of: int | None = Query(default=None, ge=0)) -> dict:
        return data().node(ref, as_of=as_of)

    @app.post("/api/nodes/{ref:path}/summary")
    def update_node_summary(ref: str, request: NodeSummaryRequest) -> dict:
        service_now = current()
        data_now = UIDataStore(service_now)
        node_now = data_now.node(ref)
        if node_now["locked"]:
            raise ValueError(f"canon 잠금 노드는 UI에서 편집할 수 없습니다: {node_now['id']}")
        if node_now["rev"] != request.rev:
            raise ValueError(
                f"리비전 충돌: 요청 r{request.rev}, 현재 r{node_now['rev']}. 다시 읽어 주세요."
            )
        summary = request.summary.strip()
        if not summary:
            raise ValueError("설명은 비워 둘 수 없습니다")
        if summary == (node_now["summary"] or ""):
            raise ValueError("변경된 설명이 없습니다")
        session_id = f"session/ui-summary-{uuid4().hex}"
        proposal = service_now.propose(
            ops=[
                {
                    "verb": "UPDATE",
                    "target": node_now["id"],
                    "field": "summary",
                    "from": node_now["summary"],
                    "to": summary,
                    "idem_key": f"ui-summary-{uuid4().hex}",
                }
            ],
            read_set=[{"node": node_now["id"], "rev": request.rev}],
            rationale="UI에서 노드 설명 편집",
            session_id=session_id,
            actor_kind="human",
            host="ui",
        )
        committed = service_now.commit(proposal["proposal_id"])
        if committed["status"] != "accepted":
            raise ValueError(
                f"설명 저장이 승인되지 않았습니다: {committed['status']}. 다시 읽어 주세요."
            )
        return {
            "proposal_id": proposal["proposal_id"],
            "status": committed["status"],
            "node": data_now.node(node_now["id"]),
        }

    @app.get("/api/search")
    def search(q: str = Query(min_length=1), as_of: int | None = Query(default=None, ge=0)):
        service_now = current()
        public_kinds = [
            name
            for name, specification in service_now.ontology.kinds.items()
            if not specification.internal
        ]
        values = service_now.find(
            q,
            kind=public_kinds,
            as_of=as_of,
            mode="hybrid",
            limit=20,
        )
        return [
            {**item, "layer": service_now.ontology.kinds[item["kind"]].layer} for item in values
        ]

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
