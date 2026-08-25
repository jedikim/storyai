import { KIND_LABELS, LAYER_LABELS } from "../format";
import type { AppStatus, GraphPayload, Layer } from "../types";

interface Props {
  graph: GraphPayload;
  status: AppStatus;
  disabledKinds: Set<string>;
  asOf: number;
  maxChapter: number;
  onToggleKind: (kind: string) => void;
  onAsOf: (value: number) => void;
}

const LAYERS: Layer[] = ["device", "event", "substance"];

export function Sidebar({
  graph,
  status,
  disabledKinds,
  asOf,
  maxChapter,
  onToggleKind,
  onAsOf,
}: Props) {
  return (
    <aside className="rail" aria-label="그래프 필터">
      <section>
        <h2>노드 타입</h2>
        {LAYERS.map((layer) => {
          const kinds = graph.nodes
            .filter((node) => node.layer === layer)
            .map((node) => node.kind)
            .filter((kind, index, values) => values.indexOf(kind) === index)
            .sort();
          if (!kinds.length) return null;
          return (
            <div className="filter-group" key={layer}>
              <h3 className={`layer-label ${layer}`}>{LAYER_LABELS[layer]}</h3>
              {kinds.map((kind) => {
                const disabled = disabledKinds.has(kind);
                return (
                  <button
                    key={kind}
                    className={`type-filter${disabled ? " is-off" : ""}`}
                    onClick={() => onToggleKind(kind)}
                    aria-pressed={!disabled}
                  >
                    <span className={`type-swatch ${layer}`} />
                    <span>{KIND_LABELS[kind] ?? kind}</span>
                    <span className="count">{graph.kind_counts[kind] ?? 0}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </section>
      <section>
        <h2>진단</h2>
        <div className="metric-row bad"><span aria-hidden="true">!</span> 미해결 진단 <b>{status.diagnostics}</b></div>
        <div className="metric-row warn"><span aria-hidden="true">◇</span> 미회수 복선 <b>{status.open_promises}</b></div>
        <div className="metric-row ok"><span aria-hidden="true">✓</span> 참조 그래프 <b>{status.edges}</b></div>
      </section>
      <section>
        <h2>기준 시점</h2>
        <label className="range-label" htmlFor="as-of">
          <span>as_of</span><b>{asOf}장</b>
        </label>
        <input
          id="as-of"
          type="range"
          min={0}
          max={Math.max(1, maxChapter)}
          value={Math.min(asOf, Math.max(1, maxChapter))}
          onChange={(event) => onAsOf(Number(event.target.value))}
        />
        <p className="rail-help">이 시점 이후에 독자에게 밝혀진 노드는 서버에서 제외됩니다.</p>
      </section>
    </aside>
  );
}
