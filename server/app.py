"""FastMCP stdio server exposing the deterministic P0-P3 tool surface."""

from __future__ import annotations

from fastmcp import FastMCP

from .runtime import get_service
from .tools.check import check
from .tools.commit import commit
from .tools.find import find
from .tools.get import get
from .tools.graph_schema import graph_schema
from .tools.impact import impact
from .tools.ingest import ingest
from .tools.neighborhood import neighborhood
from .tools.outline import outline
from .tools.promises import promises
from .tools.propose import propose
from .tools.query import query
from .tools.refs import refs
from .tools.trace import trace

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
        "제목·별칭·요약·근거 본문을 BM25, 로컬 sqlite-vec dense, 또는 두 순위의 "
        "RRF 결합으로 검색합니다. 외부 임베딩 API를 호출하지 않습니다."
    ),
    "get": (
        "주소로 노드를 읽습니다. brief는 한 줄 요약, full은 구조화 필드, body는 "
        "선택한 노드 하나의 근거 원문만 반환합니다."
    ),
    "graph_schema": (
        "런타임 온톨로지의 노드 타입·간선·태그·진단 규칙을 반환합니다. "
        "스키마를 추측하지 말고 이 도구로 확인하세요."
    ),
    "impact": (
        "가상의 필드 변경이 역방향 hard 간선을 따라 영향을 줄 노드와 관련 진단 규칙을 "
        "읽기 전용으로 미리 계산합니다."
    ),
    "ingest": (
        "원고 전체와 명시적 ID binding manifest의 해시·UTF-8 byte span·Scene 분할을 검증하고 "
        "증분 변경 Proposal만 만듭니다. live 그래프는 commit 전까지 바뀌지 않습니다."
    ),
    "neighborhood": (
        "의도 검색 seed와 명시적 anchor에서 hard 간선 1-hop을 확장해 token budget 안의 "
        "집필 컨텍스트 패킷을 만듭니다."
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
    "query": (
        "손으로 빚은 도구로 풀기 어려운 분석을 위해 단일 SELECT/WITH SQL을 읽기 전용, "
        "행 제한, 실행 예산과 함께 수행합니다."
    ),
    "refs": (
        "한 노드를 가리키거나 그 노드가 가리키는 관계를 반환합니다. "
        "산문 언급인 soft 간선은 요청할 때만 포함합니다."
    ),
    "trace": (
        "한 노드에서 특정 대상 또는 서사 장치까지의 bounded hard-edge 경로를 반환합니다. "
        "관계 종류와 깊이 및 경로 수를 제한할 수 있습니다."
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
        "impact": impact,
        "ingest": ingest,
        "neighborhood": neighborhood,
        "outline": outline,
        "propose": propose,
        "promises": promises,
        "query": query,
        "refs": refs,
        "trace": trace,
    }
    for name in sorted(functions):
        annotations = {
            "readOnlyHint": name not in {"propose", "commit", "ingest"},
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
