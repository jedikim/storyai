export type Layer = "device" | "event" | "substance";
export type Origin = "human" | "agent" | "cascade";
export type ViewName = "graph" | "promise" | "timeline" | "review";

export interface ProjectInfo {
  name: string;
  selected: boolean;
  available: boolean;
}

export interface ProjectList {
  mode: "list";
  selected: string;
  projects: ProjectInfo[];
}

export interface GraphNode {
  id: string;
  kind: string;
  layer: Layer;
  title: string;
  summary: string | null;
  props: Record<string, unknown>;
  tags: string[];
  story_from: number | null;
  story_to: number | null;
  reveal_at: number | null;
  origin: Origin;
  locked: boolean;
  rev: number;
  diagnostics: number;
}

export interface GraphEdge {
  id: number;
  source: string;
  target: string;
  rel: string;
  hard: boolean;
  origin: Origin;
  confidence: number;
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  kind_counts: Record<string, number>;
  truncated: boolean;
  cap: number;
  graph_revision: number;
  root_cid: string;
  updated_at: string | null;
}

export interface SearchResult {
  id: string;
  kind: string;
  layer: Layer;
  title: string;
  summary: string | null;
  rev: number;
  score: number;
}

export interface RefItem {
  id: string;
  kind: string;
  title: string;
  rel: string;
  direction: "in" | "out";
  hard: boolean;
}

export interface Evidence {
  file: string;
  start: number | null;
  end: number | null;
  quote: string | null;
}

export interface HistoryItem {
  rev: number;
  cid: string;
  tx_from: string;
  tx_to: string | null;
  proposal: string | null;
  origin: Origin;
  actor_kind: Origin | null;
  model_id: string | null;
  host: string | null;
  rationale: string | null;
  fields: Array<{
    field: string;
    attributed_to: string;
    on_behalf_of: string | null;
    ts: string;
  }>;
}

export interface NodeDetail extends GraphNode {
  aliases: string[];
  features: Record<string, Record<string, unknown>>;
  visible_to: Array<Record<string, unknown>>;
  body: string;
  evidence: Evidence[];
  refs: RefItem[];
  history: HistoryItem[];
}

export interface NodeUpdateResult {
  proposal_id: string;
  status: string;
  node: NodeDetail;
}

export interface PromiseItem {
  id: string;
  title: string;
  F: string[];
  T: string[];
  P: string[];
  status: "hypothetical" | "eligible" | "actualized" | "prevented";
  debt: number;
  s_eff: number | null;
  delta_coh: number | null;
}

export interface TimelinePoint {
  id: string;
  kind: string;
  title: string;
  story: number;
  story_to: number | null;
  discourse: number;
  flashback: boolean;
}

export interface TimelinePayload {
  points: TimelinePoint[];
  max_chapter: number;
}

export interface ProposalOperation {
  seq: number;
  verb: "ADD" | "UPDATE" | "INVALIDATE" | "LINK" | "UNLINK";
  target: string;
  field: string | null;
  from: unknown;
  to: unknown;
  basis_rev: number | null;
  idem_key: string;
}

export interface Proposal {
  id: string;
  status: string;
  risk: "auto" | "review" | "always";
  reasons: string[];
  conflicts: Array<Record<string, unknown>>;
  pending_overlap: Array<Record<string, unknown>>;
  actor_kind: Origin;
  model_id: string | null;
  session_id: string | null;
  host: string | null;
  rationale: string | null;
  read_set: Array<{ node: string; rev: number }>;
  ts: string;
  ops: ProposalOperation[];
}

export interface AppStatus {
  book: string;
  version: string;
  connected: boolean;
  nodes: number;
  edges: number;
  pending: number;
  diagnostics: number;
  eligible_promises: number;
  open_promises: number;
  indexed_at: string | null;
  database_bytes: number;
}
