// ============================================================================
// GraphView.tsx — live visualization of the agent graph (level 4, PLAN.md)
// ============================================================================
//
// Statically drawn nodes (Supervisor → Researcher → Synthesizer →
// Critic → END) with highlighting via SSE node events: the active node
// glows, completed ones are marked green. Deliberately NO dynamic layout —
// our graph structure is fixed, only the state changes.
//
// Technically: @xyflow/react (react-flow). Interactions are off so the
// view works like a diagram, not like a map.

import { useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

export interface GraphViewProps {
  /** Node ID that is currently running ("supervisor" | "researcher" | "synthesizer" | "critic"). */
  activeNode: string | null;
  /** Node IDs that are already completed. */
  doneNodes: string[];
  /** How many researcher instances are currently running (badge on the researcher node). */
  runningResearchers?: number;
  height?: number;
}

// Layout: fixed positions — the diagram should look like it does in PLAN.md.
const NODE_DEFS: { id: string; label: string; x: number; y: number }[] = [
  { id: "supervisor", label: "🧠 Supervisor", x: 0, y: 0 },
  { id: "researcher", label: "🔎 Researcher", x: 240, y: 0 },
  { id: "synthesizer", label: "✍️ Synthesizer", x: 480, y: 0 },
  { id: "critic", label: "🧐 Kritiker", x: 720, y: 0 },
  { id: "end", label: "🏁 Fertig", x: 960, y: 0 },
];

const EDGE_DEFS: { id: string; source: string; target: string; dashed?: boolean; label?: string }[] = [
  { id: "e1", source: "supervisor", target: "researcher", label: "Fan-out" },
  { id: "e2", source: "researcher", target: "synthesizer", label: "Join" },
  { id: "e3", source: "synthesizer", target: "critic" },
  { id: "e4", source: "critic", target: "researcher", dashed: true, label: "Lücken" },
  { id: "e5", source: "critic", target: "end", label: "ok" },
];

export function GraphView({
  activeNode,
  doneNodes,
  runningResearchers = 0,
  height = 220,
}: GraphViewProps) {
  const nodes: Node[] = useMemo(
    () =>
      NODE_DEFS.map((def) => {
        const isActive = def.id === activeNode && def.id !== "end";
        const isDone = doneNodes.includes(def.id);
        let style: React.CSSProperties = {
          border: "1px solid rgb(var(--border))",
          borderRadius: "10px",
          background: "rgb(var(--card))",
          padding: "10px 14px",
          fontSize: 13,
          fontWeight: 500,
          width: 150,
          textAlign: "center",
          boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
        };
        if (isActive) {
          style = {
            ...style,
            borderColor: "#3b82f6",
            boxShadow: "0 0 0 3px rgba(59,130,246,0.25)",
            background: "rgba(59,130,246,0.08)",
          };
        } else if (isDone) {
          style = {
            ...style,
            borderColor: "#22c55e",
            background: "rgba(34,197,94,0.08)",
          };
        }
        const badge =
          def.id === "researcher" && runningResearchers > 0
            ? ` ×${runningResearchers}`
            : "";
        return {
          id: def.id,
          position: { x: def.x, y: def.y },
          data: { label: `${def.label}${badge}` },
          style,
          draggable: false,
          selectable: false,
        };
      }),
    [activeNode, doneNodes, runningResearchers],
  );

  const edges: Edge[] = useMemo(
    () =>
      EDGE_DEFS.map((def) => ({
        id: def.id,
        source: def.source,
        target: def.target,
        label: def.label,
        animated: def.source === activeNode,
        style: {
          stroke: def.dashed ? "#f59e0b" : "#94a3b8",
          strokeDasharray: def.dashed ? "6 4" : undefined,
        },
        labelStyle: { fontSize: 11, fill: "#64748b" },
        labelBgStyle: { fill: "rgb(var(--card))" },
      })),
    [activeNode],
  );

  return (
    <div style={{ height, border: "1px solid rgb(var(--border))", borderRadius: "12px" }} className="bg-card p-2">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
      >
        <Background gap={16} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
