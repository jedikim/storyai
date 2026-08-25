import { formatValue } from "../format";
import type { PromiseItem } from "../types";

const COLUMNS: Array<{
  status: PromiseItem["status"];
  label: string;
  description: string;
}> = [
  { status: "hypothetical", label: "가설", description: "심겼지만 트리거 미정" },
  { status: "eligible", label: "회수 가능", description: "트리거 발화, 회수 대기" },
  { status: "actualized", label: "회수 완료", description: "독자에게 보상됨" },
  { status: "prevented", label: "의도적 폐기", description: "레드헤링 또는 중단" },
];

interface Props {
  items: PromiseItem[];
  onSelect: (id: string) => void;
}

function metric(value: number | null): string {
  return value === null ? "—" : value.toFixed(2);
}

export function PromiseBoard({ items, onSelect }: Props) {
  return (
    <section className="board-view" aria-labelledby="promise-title">
      <header className="view-heading">
        <div><span className="eyebrow">F–T–P STATE MACHINE</span><h1 id="promise-title">복선 보드</h1></div>
        <p>설정 F, 트리거 T, 회수 P를 분리해 “아직 안 터진 복선”과 “근거 없는 해결”을 구분합니다.</p>
      </header>
      <div className="promise-columns">
        {COLUMNS.map((column) => {
          const values = items.filter((item) => item.status === column.status);
          return (
            <section className={`promise-column status-${column.status}`} key={column.status}>
              <header><div><h2>{column.label}</h2><p>{column.description}</p></div><span>{values.length}</span></header>
              <div className="promise-stack">
                {values.map((item) => (
                  <button className="promise-card" key={item.id} onClick={() => onSelect(item.id)}>
                    <span className="promise-card__id">{item.id}</span>
                    <h3>{item.title}</h3>
                    <dl className="ftp-grid">
                      <div><dt>F</dt><dd>{formatValue(item.F)}</dd></div>
                      <div><dt>T</dt><dd>{formatValue(item.T)}</dd></div>
                      <div><dt>P</dt><dd>{formatValue(item.P)}</dd></div>
                    </dl>
                    <div className="promise-metrics">
                      <span>S-Eff <b>{metric(item.s_eff)}</b></span>
                      <span>Δ-Coh <b>{metric(item.delta_coh)}</b></span>
                      <span>부채 <b>{item.debt.toFixed(2)}</b></span>
                    </div>
                  </button>
                ))}
                {!values.length && <div className="column-empty">이 상태의 복선이 없습니다.</div>}
              </div>
            </section>
          );
        })}
      </div>
    </section>
  );
}
