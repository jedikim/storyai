"""FastMCP stdio server exposing the deterministic P0-P2 tool surface."""

from __future__ import annotations

from fastmcp import FastMCP

from .runtime import get_service
from .tools.check import check
from .tools.commit import commit
from .tools.find import find
from .tools.get import get
from .tools.graph_schema import graph_schema
from .tools.outline import outline
from .tools.promises import promises
from .tools.propose import propose
from .tools.refs import refs

INSTRUCTIONS = (
    "storyai는 소설 설정과 서사 관계를 읽는 로컬 그래프입니다. "
    "outline 또는 find로 주소를 좁히고 get(include='brief')를 먼저 사용하세요. "
    "본문은 꼭 필요한 노드 하나에만 get(include='body')로 요청하세요. "
    "refs는 역방향 관계를 찾으며 soft 언급은 기본 제외됩니다."
    " 집필 전에는 check와 promises로 연속성 오류와 회수 가능한 복선을 확인하세요."
)

TOOL_DESCRIPTIONS = {
    "check": (
        "spec/rules.json의 P2 SQL 규칙으로 책 또는 노드 범위의 연속성을 진단합니다. "
        "LLM을 호출하지 않으며 노드와 근거를 함께 반환합니다."
    ),
    "commit": (
        "저장된 제안을 단일 SQLite 커밋 레인에서 원자적으로 적용하거나 dry_run 합니다. "
        "충돌하면 어떤 변경도 적용하지 않습니다."
    ),
    "find": (
        "제목·별칭·요약·설정집 본문을 검색합니다. P0의 hybrid는 BM25/문자열 검색이며 "
        "semantic 전용 모드는 P3에서 추가됩니다."
    ),
    "get": (
        "주소로 노드를 읽습니다. brief는 한 줄 요약, full은 구조화 필드, body는 "
        "선택한 노드 하나의 근거 원문만 반환합니다."
    ),
    "graph_schema": (
        "런타임 온톨로지의 노드 타입·간선·태그·진단 규칙을 반환합니다. "
        "스키마를 추측하지 말고 이 도구로 확인하세요."
    ),
    "outline": (
        "책 전체 또는 한 주소 아래의 구조를 본문 없이 반환합니다. "
        "항상 넓은 탐색의 첫 단계로 사용하세요."
    ),
    "propose": (
        "비어 있지 않은 read_set과 멱등 키가 있는 연산을 변경 제안으로 기록합니다. "
        "이 호출만으로 live 그래프는 바뀌지 않습니다."
    ),
    "promises": (
        "복선의 F-T-P, 상태, 부채, S-Eff, delta-Coh 근사치를 조회합니다. "
        "집필 전에 eligible 상태만 필터링할 수 있습니다."
    ),
    "refs": (
        "한 노드를 가리키거나 그 노드가 가리키는 관계를 반환합니다. "
        "산문 언급인 soft 간선은 요청할 때만 포함합니다."
    ),
}


def create_server() -> FastMCP:
    get_service()
    server = FastMCP(name="storyai", instructions=INSTRUCTIONS)
    functions = {
        "check": check,
        "commit": commit,
        "find": find,
        "get": get,
        "graph_schema": graph_schema,
        "outline": outline,
        "propose": propose,
        "promises": promises,
        "refs": refs,
    }
    for name in sorted(functions):
        annotations = {
            "readOnlyHint": name not in {"propose", "commit"},
            "destructiveHint": name == "commit",
            "idempotentHint": True,
            "openWorldHint": False,
        }
        server.tool(
            name=name,
            description=TOOL_DESCRIPTIONS[name],
            annotations=annotations,
        )(functions[name])
    return server


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
