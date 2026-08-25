import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import { KIND_LABELS, ORIGIN_LABELS } from "../format";
import type { GraphNode } from "../types";

export type StoryFlowNode = Node<
  { story: GraphNode; dimmed?: boolean; selectedNode?: boolean },
  "story"
>;

export function StoryNode({ data }: NodeProps<StoryFlowNode>) {
  const node = data.story;
  return (
    <article
      className={`story-node layer-${node.layer}${data.dimmed ? " is-dimmed" : ""}${
        data.selectedNode ? " is-selected" : ""
      }`}
      aria-label={`${node.kind} ${node.title}`}
      data-node-id={node.id}
    >
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="story-node__meta">
        <span>{node.kind}</span>
        <span aria-label={`출처: ${ORIGIN_LABELS[node.origin]}`} className={`origin-dot ${node.origin}`} />
        {node.locked && <span title="canon 잠금">◆</span>}
        {node.diagnostics > 0 && (
          <span className="diagnostic-count" title="미해결 진단">
            !{node.diagnostics}
          </span>
        )}
      </div>
      <h3>{node.title}</h3>
      <p>{node.summary || KIND_LABELS[node.kind] || node.kind}</p>
      <div className="story-node__tags">
        {node.tags.slice(0, 3).map((tag) => (
          <span key={tag}>{tag}</span>
        ))}
        {node.tags.length > 3 && <span>+{node.tags.length - 3}</span>}
      </div>
      <Handle type="source" position={Position.Right} className="node-handle" />
    </article>
  );
}
