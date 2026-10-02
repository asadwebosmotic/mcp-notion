"use client";

import React, { useEffect, useState } from "react";
import { Server, Brain, Code, Cpu, Database, ChevronDown, Zap, CheckCircle2, CircleDashed, Wrench, XCircle } from "lucide-react";

interface TraceEvent {
  id: string;
  type: "status" | "tool_call" | "tool_result" | "error" | "done";
  title: string;
  detail?: string;
}

interface ExecutionTraceProps {
  isGenerating: boolean;
  healthStatus: { status: string; mcp_connected: boolean; tools_count: number } | null;
  traceEvents: TraceEvent[];
}

export default function ExecutionTrace({
  isGenerating,
  healthStatus,
  traceEvents,
}: ExecutionTraceProps) {
  const steps = [
    {
      id: "frontend",
      name: "Next.js Frontend",
      desc: "User UI & Session Client",
      icon: Code,
      color: "from-violet-500 to-indigo-500",
      activeColor: "shadow-violet-500/30 text-violet-400",
    },
    {
      id: "fastapi",
      name: "FastAPI Gateway",
      desc: "REST router & session store",
      icon: Server,
      color: "from-indigo-500 to-cyan-500",
      activeColor: "shadow-indigo-500/30 text-indigo-400",
    },
    {
      id: "gemini",
      name: "Gemini Orchestrator",
      desc: "Flash 2.5 Agent Reasoning",
      icon: Brain,
      color: "from-purple-500 to-pink-500",
      activeColor: "shadow-purple-500/30 text-purple-400",
    },
    {
      id: "mcp",
      name: "MCP Tool Layer",
      desc: "FastMCP server definitions",
      icon: Cpu,
      color: "from-violet-600 to-fuchsia-600",
      activeColor: "shadow-violet-600/30 text-fuchsia-400",
    },
    {
      id: "external",
      name: "External APIs",
      desc: "Notion · Gmail · Calendar · Slack",
      icon: Database,
      color: "from-emerald-500 to-teal-500",
      activeColor: "shadow-emerald-500/30 text-emerald-400",
    },
  ];

  const [activeStepIndex, setActiveStepIndex] = useState<number | null>(null);

  useEffect(() => {
    const startTimer = window.setTimeout(() => {
      setActiveStepIndex(isGenerating ? 0 : null);
    }, 0);

    if (!isGenerating) {
      return () => window.clearTimeout(startTimer);
    }

    const interval = window.setInterval(() => {
      setActiveStepIndex((current) => {
        if (current === null) return 0;
        return Math.min(current + 1, steps.length - 1);
      });
    }, 900);

    return () => {
      window.clearTimeout(startTimer);
      window.clearInterval(interval);
    };
  }, [isGenerating, steps.length]);

  return (
    <aside className="w-80 glass-panel border-l border-white/10 p-6 flex flex-col h-full overflow-y-auto shrink-0 z-10">
      <div className="flex items-center space-x-2 pb-4 border-b border-white/10 mb-6">
        <Zap className="w-4 h-4 text-violet-400 animate-pulse" />
        <h2 className="text-sm font-bold tracking-wider uppercase text-gray-200">
          Execution Flow Trace
        </h2>
      </div>

      <div className="flex-1 flex flex-col py-2 relative">
        {steps.map((step, idx) => {
          const StepIcon = step.icon;
          const isActive = isGenerating && activeStepIndex === idx;
          const isComplete = isGenerating && activeStepIndex !== null && idx < activeStepIndex;
          const isConnectorActive = isGenerating && activeStepIndex === idx && idx < steps.length - 1;

          let statusText = "Ready";
          if (isGenerating) {
            if (isComplete) {
              statusText = "Done";
            } else if (!isActive) {
              statusText = "Waiting";
            } else if (step.id === "frontend") statusText = "Sending";
            else if (step.id === "fastapi") statusText = "Routing";
            else if (step.id === "gemini") statusText = "Reasoning";
            else if (step.id === "mcp") statusText = "Using Tool";
            else if (step.id === "external") statusText = "Calling API";
          } else {
            statusText = step.id === "external" && healthStatus?.mcp_connected
              ? "Linked"
              : "Idle";
          }

          return (
            <div key={step.id} className="relative z-10">
              <div className="flex items-start space-x-4 group">
                <div className={`w-14 h-14 rounded-2xl flex items-center justify-center transition-all duration-500 border ${isActive
                    ? `bg-white/5 border-violet-500/60 shadow-md ${step.activeColor} scale-105 animate-pulse`
                    : isComplete
                      ? "bg-white/5 border-emerald-400/30 text-emerald-400"
                      : "bg-black/40 border-white/10 text-gray-500"
                  }`}>
                  <StepIcon className="w-6 h-6" />
                </div>

                <div className="flex-1 min-w-0 pt-1">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className={`text-xs font-semibold uppercase tracking-wider transition-colors duration-300 ${isActive ? "text-white" : isComplete ? "text-gray-300" : "text-gray-400"
                      }`}>
                      {step.name}
                    </h3>
                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border transition-all duration-300 shrink-0 ${isActive
                        ? "bg-violet-500/10 border-violet-500/25 text-violet-300 animate-pulse"
                        : isComplete
                          ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-300"
                          : "bg-white/5 border-white/5 text-gray-600"
                      }`}>
                      {statusText}
                    </span>
                  </div>
                  <p className="text-[11px] text-gray-500 truncate mt-0.5">{step.desc}</p>
                </div>
              </div>

              {idx < steps.length - 1 && (
                <div className="ml-[27px] h-20 flex flex-col items-center justify-center pointer-events-none">
                  <div className={`w-0.5 flex-1 transition-colors duration-500 ${isComplete ? "bg-emerald-400/35" : "bg-white/5"}`} />
                  <ChevronDown
                    className={`w-5 h-5 my-1 transition-all duration-300 ${isConnectorActive
                        ? "text-violet-300 animate-bounce drop-shadow-[0_0_8px_rgba(139,92,246,0.9)]"
                        : isComplete
                          ? "text-emerald-300/70"
                          : "text-white/10"
                      }`}
                  />
                  <div className={`w-0.5 flex-1 transition-colors duration-500 ${isComplete ? "bg-emerald-400/35" : "bg-white/5"}`} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-6 pt-4 border-t border-white/10 text-[10px] text-gray-500 leading-normal">
        <div className="mb-5">
          <p className="mb-2 font-medium text-gray-400">Live run:</p>
          <div className="space-y-2">
            {traceEvents.length === 0 ? (
              <p className="rounded-lg border border-white/5 bg-white/[0.03] px-3 py-2 text-gray-600">
                Waiting for the next request.
              </p>
            ) : (
              traceEvents.map((event) => {
                const Icon =
                  event.type === "tool_call"
                    ? Wrench
                    : event.type === "tool_result" || event.type === "done"
                      ? CheckCircle2
                      : event.type === "error"
                        ? XCircle
                        : CircleDashed;
                const tone =
                  event.type === "tool_result" || event.type === "done"
                    ? "text-emerald-300 border-emerald-400/15 bg-emerald-400/5"
                    : event.type === "error"
                      ? "text-rose-300 border-rose-400/15 bg-rose-400/5"
                      : event.type === "tool_call"
                        ? "text-cyan-300 border-cyan-400/15 bg-cyan-400/5"
                        : "text-violet-300 border-violet-400/15 bg-violet-400/5";

                return (
                  <div
                    key={event.id}
                    className={`rounded-lg border px-3 py-2 ${tone}`}
                  >
                    <div className="flex items-start gap-2">
                      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <div className="min-w-0">
                        <p className="truncate text-[11px] font-medium text-gray-200">
                          {event.title}
                        </p>
                        {event.detail && (
                          <p className="mt-1 line-clamp-2 break-all font-mono text-[9px] leading-snug text-gray-500">
                            {event.detail}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
        <p className="mb-2 font-medium text-gray-400">How it works:</p>
        <p>
          When you enter a prompt, Next.js calls FastAPI, which starts the Gemini orchestrator.
          Gemini decides if it needs tools, queries the MCP Python server, and issues authenticated commands to Notion, Google, or Slack APIs.
        </p>
      </div>
    </aside>
  );
}
