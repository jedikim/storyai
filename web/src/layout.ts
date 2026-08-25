import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "@xyflow/react";

import type { GraphEdge, GraphNode } from "./types";

const WIDTH = 186;
const HEIGHT = 96;

export async function layoutGraph(
  storyNodes: GraphNode[],
  storyEdges: GraphEdge[],
): Promise<{ nodes: Node[]; edges: Edge[] }> {
  const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
  const elk = new ELK();
  const graph: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "42",
      "elk.layered.spacing.nodeNodeBetweenLayers": "92",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.edgeRouting": "SPLINES",
      "elk.separateConnectedComponents": "true",
    },
    children: storyNodes.map((node) => ({ id: node.id, width: WIDTH, height: HEIGHT })),
    edges: storyEdges.map(
      (edge): ElkExtendedEdge => ({
        id: String(edge.id),
        sources: [edge.source],
        targets: [edge.target],
      }),
    ),
  };
  const result = await elk.layout(graph);
  const positions = new Map(
    (result.children ?? []).map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]),
  );
  return {
    nodes: storyNodes.map((node) => ({
      id: node.id,
      type: "story",
      position: positions.get(node.id) ?? { x: 0, y: 0 },
      data: { story: node },
    })),
    edges: storyEdges.map((edge) => ({
      id: String(edge.id),
      source: edge.source,
      target: edge.target,
      label: edge.rel,
      data: { story: edge },
    })),
  };
}
