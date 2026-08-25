import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import { useEffect, useMemo, useRef } from "react";

import { layoutGraph } from "../layout";
import type { GraphPayload } from "../types";
import { StoryNode } from "./StoryNode";

const nodeTypes = { story: StoryNode };
const THREAD_RELATIONS = new Set(["plants", "requires_trigger", "pays_off"]);

interface Props {
  graph: GraphPayload;
  selectedId: string | null;
  disabledKinds: Set<string>;
  onSelect: (id: string | null) => void;
}

function Flow({ graph, selectedId, disabledKinds, onSelect }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();
  const layoutVersion = useRef(0);

  useEffect(() => {
    const version = ++layoutVersion.current;
    void layoutGraph(graph.nodes, graph.edges).then((value) => {
      if (version !== layoutVersion.current) return;
      setNodes(value.nodes);
      setEdges(value.edges);
      window.requestAnimationFrame(() => void fitView({ padding: 0.18, duration: 250 }));
    });
  }, [fitView, graph.edges, graph.nodes, setEdges, setNodes]);

  const visibleNodes = useMemo(
    () =>
      nodes.map((node) => {
        const story = graph.nodes.find((item) => item.id === node.id);
        return {
          ...node,
          data: {
            ...node.data,
            dimmed: story ? disabledKinds.has(story.kind) : false,
            selectedNode: selectedId === node.id,
          },
          tabIndex: 0,
        };
      }),
    [disabledKinds, graph.nodes, nodes, selectedId],
  );

  const visibleEdges = useMemo(
    () =>
      edges.map((edge) => {
        const story = graph.edges.find((item) => String(item.id) === edge.id);
        const source = graph.nodes.find((item) => item.id === edge.source);
        const target = graph.nodes.find((item) => item.id === edge.target);
        const dimmed = Boolean(
          (source && disabledKinds.has(source.kind)) || (target && disabledKinds.has(target.kind)),
        );
        const adjacent = selectedId === edge.source || selectedId === edge.target;
        const thread = story ? THREAD_RELATIONS.has(story.rel) : false;
        const color = thread ? "var(--thread)" : "var(--muted)";
        return {
          ...edge,
          label: adjacent && !dimmed ? edge.label : undefined,
          animated: false,
          style: {
            stroke: color,
            strokeWidth: thread ? (adjacent ? 3 : 2) : adjacent ? 2 : 1.2,
            strokeDasharray: story?.hard === false ? "5 5" : undefined,
            opacity: dimmed ? 0.08 : adjacent ? 1 : thread ? 0.72 : 0.42,
          },
          labelStyle: { fill: color, fontFamily: "var(--mono)", fontSize: 10 },
          labelBgStyle: { fill: "var(--paper)", fillOpacity: 0.9 },
        };
      }),
    [disabledKinds, edges, graph.edges, graph.nodes, selectedId],
  );

  return (
    <ReactFlow
      nodes={visibleNodes}
      edges={visibleEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => onSelect(node.id)}
      onPaneClick={() => onSelect(null)}
      minZoom={0.2}
      maxZoom={2}
      fitView
      nodesDraggable
      nodesConnectable={false}
      elementsSelectable
      proOptions={{ hideAttribution: false }}
      aria-label="서사 그래프 캔버스"
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} />
      <MiniMap
        nodeColor={(node) => {
          const layer = (node.data.story as { layer: string }).layer;
          return layer === "device"
            ? "var(--thread)"
            : layer === "event"
              ? "var(--indigo)"
              : "var(--moss)";
        }}
        maskColor="color-mix(in srgb, var(--paper) 75%, transparent)"
        pannable
        zoomable
      />
      <Controls showInteractive={false} />
      <div className="edge-legend" aria-label="간선 범례">
        <span><i className="line hard" />hard — 구조 관계</span>
        <span><i className="line soft" />soft — 산문 언급</span>
        <span><i className="line thread" />복선 실</span>
      </div>
      {graph.truncated && (
        <div className="graph-cap" role="status">
          그래프가 {graph.cap}개 노드 상한에 도달했습니다. 검색과 시점 필터로 좁혀 주세요.
        </div>
      )}
    </ReactFlow>
  );
}

export function GraphView(props: Props) {
  return (
    <section className="graph-stage" aria-label="그래프 뷰">
      <ReactFlowProvider>
        <Flow {...props} />
      </ReactFlowProvider>
    </section>
  );
}
