import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import { GraphView } from "./components/GraphView";
import { Inspector } from "./components/Inspector";
import { OmniSearch } from "./components/OmniSearch";
import { PromiseBoard } from "./components/PromiseBoard";
import { ProjectSwitcher } from "./components/ProjectSwitcher";
import { ReviewQueue } from "./components/ReviewQueue";
import { Sidebar } from "./components/Sidebar";
import { TimelineView } from "./components/TimelineView";
import { formatBytes, formatDate } from "./format";
import type {
  AppStatus,
  GraphPayload,
  NodeDetail,
  PromiseItem,
  ProjectList,
  Proposal,
  TimelinePayload,
  ViewName,
} from "./types";

type Theme = "auto" | "light" | "dark";

const VIEWS: Array<{ id: ViewName; label: string; short: string }> = [
  { id: "graph", label: "그래프", short: "G" },
  { id: "promise", label: "복선", short: "P" },
  { id: "timeline", label: "시간축", short: "T" },
  { id: "review", label: "검수", short: "R" },
];

function defaultStatus(): AppStatus {
  return {
    book: "storyai",
    version: "—",
    connected: false,
    nodes: 0,
    edges: 0,
    pending: 0,
    diagnostics: 0,
    eligible_promises: 0,
    open_promises: 0,
    indexed_at: null,
    database_bytes: 0,
  };
}

function defaultGraph(): GraphPayload {
  return {
    nodes: [],
    edges: [],
    kind_counts: {},
    truncated: false,
    cap: 500,
    graph_revision: 0,
    root_cid: "",
    updated_at: null,
  };
}

const EMPTY_PROJECTS: ProjectList = { mode: "list", selected: "", projects: [] };

function App() {
  const [view, setView] = useState<ViewName>("graph");
  const [status, setStatus] = useState<AppStatus>(defaultStatus);
  const [graph, setGraph] = useState<GraphPayload>(defaultGraph);
  const [promises, setPromises] = useState<PromiseItem[]>([]);
  const [timeline, setTimeline] = useState<TimelinePayload>({ points: [], max_chapter: 1 });
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [projects, setProjects] = useState<ProjectList>(EMPTY_PROJECTS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [disabledKinds, setDisabledKinds] = useState<Set<string>>(new Set());
  const [asOf, setAsOf] = useState(1);
  const [theme, setTheme] = useState<Theme>("auto");
  const [loading, setLoading] = useState(true);
  const [switchingProject, setSwitchingProject] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filterRequest = useRef(0);
  const loadRequest = useRef(0);

  const load = useCallback(async (chapter?: number) => {
    const request = ++loadRequest.current;
    setLoading(true);
    setError(null);
    try {
      const nextProjects = await api.projects();
      const [nextStatus, nextTimeline, nextProposals] = await Promise.all([
        api.health(),
        api.timeline(),
        api.proposals(),
      ]);
      const nextAsOf = chapter ?? Math.max(1, nextTimeline.max_chapter);
      const [nextGraph, nextPromises] = await Promise.all([
        api.graph(nextAsOf),
        api.promises(nextAsOf),
      ]);
      if (request !== loadRequest.current) return;
      setProjects(nextProjects);
      setStatus(nextStatus);
      setTimeline(nextTimeline);
      setProposals(nextProposals);
      setGraph(nextGraph);
      setPromises(nextPromises);
      setAsOf(nextAsOf);
    } catch (reason) {
      if (request === loadRequest.current) setError((reason as Error).message);
    } finally {
      if (request === loadRequest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedId(null);
      if (event.altKey) {
        const next = VIEWS[Number(event.key) - 1];
        if (next) setView(next.id);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  const activeTitle = useMemo(() => VIEWS.find((item) => item.id === view)?.label ?? "그래프", [view]);

  function selectNode(id: string) {
    setSelectedId(id);
    setView("graph");
  }

  function updateNode(updated: NodeDetail) {
    setGraph((value) => ({
      ...value,
      nodes: value.nodes.map((item) =>
        item.id === updated.id
          ? { ...item, summary: updated.summary, rev: updated.rev, origin: updated.origin }
          : item,
      ),
    }));
  }

  function toggleKind(kind: string) {
    setDisabledKinds((values) => {
      const next = new Set(values);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  function changeAsOf(value: number) {
    const request = ++filterRequest.current;
    setSelectedId(null);
    setAsOf(value);
    void Promise.all([api.graph(value), api.promises(value)])
      .then(([nextGraph, nextPromises]) => {
        if (request !== filterRequest.current) return;
        setGraph(nextGraph);
        setPromises(nextPromises);
      })
      .catch((reason: Error) => {
        if (request === filterRequest.current) setError(reason.message);
      });
  }

  function cycleTheme() {
    setTheme((value) => (value === "auto" ? "light" : value === "light" ? "dark" : "auto"));
  }

  async function selectProject(name: string) {
    if (name === projects.selected) return;
    setSwitchingProject(true);
    setSelectedId(null);
    setDisabledKinds(new Set());
    setStatus(defaultStatus());
    setGraph(defaultGraph());
    setPromises([]);
    setTimeline({ points: [], max_chapter: 1 });
    setProposals([]);
    filterRequest.current += 1;
    loadRequest.current += 1;
    try {
      await api.selectProject(name);
      await load();
    } catch (reason) {
      setError((reason as Error).message);
      setLoading(false);
    } finally {
      setSwitchingProject(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#graph" onClick={() => setView("graph")} aria-label="storyai 홈">
          <span className="brand-mark">S</span>
          <span><b>storyai</b><small>narrative graph</small></span>
        </a>
        <ProjectSwitcher
          projects={projects}
          busy={switchingProject}
          onSelect={(name) => void selectProject(name)}
        />
        <nav className="view-tabs" aria-label="주요 보기">
          {VIEWS.map((item, index) => (
            <button
              key={item.id}
              className={view === item.id ? "is-active" : ""}
              onClick={() => setView(item.id)}
              aria-current={view === item.id ? "page" : undefined}
              title={`Alt+${index + 1}`}
            >
              <span>{item.short}</span>{item.label}
              {item.id === "review" && status.pending > 0 && <i>{status.pending}</i>}
            </button>
          ))}
        </nav>
        <OmniSearch key={projects.selected} asOf={asOf} onSelect={selectNode} />
        <button className="icon-button theme-button" onClick={cycleTheme} title={`테마: ${theme}`} aria-label={`테마 전환, 현재 ${theme}`}>
          {theme === "dark" ? "●" : theme === "light" ? "○" : "◐"}
        </button>
        <div className={`connection${status.connected ? " is-live" : ""}`} title="서버 연결 상태">
          <i /> <span>{status.connected ? "연결됨" : "연결 끊김"}</span>
        </div>
      </header>

      <main className={`workspace view-${view}`} aria-label={`${activeTitle} 작업공간`}>
        {error && <div className="global-error" role="alert"><b>데이터를 불러오지 못했습니다.</b><span>{error}</span><button onClick={() => void load(asOf)}>다시 시도</button></div>}
        {loading && <div className="loading-scrim" role="status"><span />서사 그래프 동기화 중…</div>}
        {view === "graph" && (
          <div className="graph-shell">
            <Sidebar
              graph={graph}
              status={status}
              disabledKinds={disabledKinds}
              asOf={asOf}
              maxChapter={timeline.max_chapter}
              onToggleKind={toggleKind}
              onAsOf={changeAsOf}
            />
            <GraphView graph={graph} selectedId={selectedId} disabledKinds={disabledKinds} onSelect={setSelectedId} />
            <Inspector
              nodeId={selectedId}
              asOf={asOf}
              maxChapter={timeline.max_chapter}
              onSelect={selectNode}
              onClose={() => setSelectedId(null)}
              onUpdated={updateNode}
            />
          </div>
        )}
        {view === "promise" && <PromiseBoard items={promises} onSelect={selectNode} />}
        {view === "timeline" && <TimelineView timeline={timeline} onSelect={selectNode} />}
        {view === "review" && <ReviewQueue proposals={proposals} onReload={() => load(asOf)} />}
      </main>

      <footer className="statusbar">
        <span><i className="status-dot" />graph r{graph.graph_revision}</span>
        <span>{status.nodes} nodes · {status.edges} edges</span>
        <span>{status.diagnostics ? `${status.diagnostics} diagnostics` : "✓ no errors"}</span>
        <span className="status-spacer" />
        <span>indexed {formatDate(status.indexed_at)}</span>
        <span>{formatBytes(status.database_bytes)}</span>
        <span>v{status.version}</span>
      </footer>
    </div>
  );
}

export default App;
