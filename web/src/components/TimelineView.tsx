import type { TimelinePayload } from "../types";

interface Props {
  timeline: TimelinePayload;
  onSelect: (id: string) => void;
}

export function TimelineView({ timeline, onSelect }: Props) {
  const width = Math.max(760, timeline.max_chapter * 72 + 160);
  const height = Math.max(360, timeline.max_chapter * 34 + 100);
  const x = (chapter: number) => 100 + (chapter / Math.max(1, timeline.max_chapter)) * (width - 150);
  const y = (chapter: number) => 50 + (chapter / Math.max(1, timeline.max_chapter)) * (height - 100);
  const ordered = [...timeline.points].sort((a, b) => a.discourse - b.discourse || a.story - b.story);
  const path = ordered.map((point, index) => `${index ? "L" : "M"} ${x(point.discourse)} ${y(point.story)}`).join(" ");
  return (
    <section className="board-view timeline-view" aria-labelledby="timeline-title">
      <header className="view-heading">
        <div><span className="eyebrow">STORY × DISCOURSE</span><h1 id="timeline-title">이중 시간축</h1></div>
        <p>가로는 독자가 알게 된 순서, 세로는 작중 실제 시간입니다. 아래로 되감기는 지점이 회상입니다.</p>
      </header>
      <div className="timeline-card">
        {ordered.length ? (
          <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="작중 시간과 독자 공개 시점 비교">
            <defs>
              <pattern id="timeline-grid" width="72" height="34" patternUnits="userSpaceOnUse">
                <path d="M 72 0 L 0 0 0 34" fill="none" stroke="var(--line-soft)" strokeWidth="1" />
              </pattern>
            </defs>
            <rect x="100" y="50" width={width - 150} height={height - 100} fill="url(#timeline-grid)" />
            <text x={width / 2} y="24" textAnchor="middle" className="axis-label">discourse · 독자 공개 순서 →</text>
            <text transform={`translate(22 ${height / 2}) rotate(-90)`} textAnchor="middle" className="axis-label">story · 작중 실제 시간 →</text>
            <line x1="100" y1="50" x2={width - 50} y2={height - 50} className="chronological-line" />
            <path d={path} className="narrative-line" />
            {ordered.map((point) => (
              <g
                key={point.id}
                className={`timeline-point${point.flashback ? " flashback" : ""}`}
                onClick={() => onSelect(point.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => event.key === "Enter" && onSelect(point.id)}
              >
                <circle cx={x(point.discourse)} cy={y(point.story)} r={point.flashback ? 7 : 5} />
                <text x={x(point.discourse) + 10} y={y(point.story) - 8}>{point.title}</text>
                {point.flashback && <text x={x(point.discourse) + 10} y={y(point.story) + 8} className="flashback-label">↺ 회상</text>}
              </g>
            ))}
          </svg>
        ) : (
          <div className="empty-board"><span>↺</span><h2>시간 정보가 아직 없습니다</h2><p>story_from과 reveal_at이 있는 노드가 생기면 자동으로 그려집니다.</p></div>
        )}
      </div>
    </section>
  );
}
