"use client";

import React, { useState, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";
import ChatContainer from "@/components/ChatContainer";
import ExecutionTrace from "@/components/ExecutionTrace";
import confetti from "canvas-confetti";
import { AlertCircle, RefreshCw } from "lucide-react";

interface SessionInfo {
  session_id: string;
  created_at: string;
  message_count: number;
}

interface Message {
  role: "user" | "model";
  parts: Array<{ text: string }>;
}

interface HealthStatus {
  status: string;
  mcp_connected: boolean;
  tools_count: number;
}

export interface TraceEvent {
  id: string;
  type: "status" | "tool_call" | "tool_result" | "error" | "done";
  title: string;
  detail?: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

const isAbortError = (err: unknown) =>
  err instanceof DOMException && err.name === "AbortError";

interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

const parseSseEvent = (rawEvent: string): StreamEvent | null => {
  const lines = rawEvent.split(/\r?\n/);
  let event = "message";
  let data = "";

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      data += line.slice(5).trim();
    }
  }

  if (!data) return null;

  return {
    event,
    data: JSON.parse(data) as Record<string, unknown>,
  };
};

export default function Home() {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [healthStatus, setHealthStatus] = useState<HealthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);

  const addTraceEvent = (
    requestId: number,
    event: Omit<TraceEvent, "id">,
  ) => {
    setTraceEvents((prev) => [
      ...prev.slice(-11),
      {
        ...event,
        id: `${requestId}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      },
    ]);
  };

  // 1. Fetch Sessions List
  const fetchSessions = async () => {
    try {
      const res = await fetch(`${API_BASE}/sessions`);
      if (!res.ok) throw new Error("Failed to load sessions");
      const data = await res.json();
      setSessions(data);
      setError(null);
    } catch (err: unknown) {
      console.error(err);
      setError("Could not connect to FastAPI server. Make sure it is running on port 8000.");
    }
  };

  // 2. Fetch Server Health
  const checkHealth = async () => {
    try {
      const res = await fetch(`${API_BASE}/health`);
      if (res.ok) {
        const data = await res.json();
        setHealthStatus(data);
      } else {
        setHealthStatus(null);
      }
    } catch {
      setHealthStatus(null);
    }
  };

  // 3. Load Session Details (History)
  const loadSession = async (sessionId: string) => {
    try {
      handleStopGeneration();
      const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
      if (!res.ok) throw new Error("Failed to load session details");
      const data = await res.json();
      setMessages(data.history || []);
      setActiveSessionId(sessionId);
    } catch (err: unknown) {
      console.error(err);
      setError("Failed to load conversation history.");
    }
  };

  // 4. Create New Session
  const handleCreateSession = async () => {
    try {
      const res = await fetch(`${API_BASE}/sessions`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to create session");
      const newSession: SessionInfo = await res.json();

      setSessions((prev) => [newSession, ...prev]);
      setActiveSessionId(newSession.session_id);
      setMessages([]);
      setError(null);
    } catch (err: unknown) {
      console.error(err);
      setError("Failed to create new conversation.");
    }
  };

  // 5. Delete Session
  const handleDeleteSession = async (sessionId: string) => {
    try {
      const res = await fetch(`${API_BASE}/sessions/${sessionId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete session");

      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setMessages([]);
      }
    } catch (err: unknown) {
      console.error(err);
      setError("Failed to delete conversation.");
    }
  };

  // 6. Send Message to Agent
  const handleSendMessage = async (text: string) => {
    let currentSessionId = activeSessionId;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    abortControllerRef.current?.abort();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    // Optimistic UI updates
    const newUserMsg: Message = { role: "user", parts: [{ text }] };
    setMessages((prev) => [...prev, newUserMsg]);
    setIsGenerating(true);
    setError(null);
    setTraceEvents([]);
    addTraceEvent(requestId, {
      type: "status",
      title: "Sending prompt",
      detail: "Next.js opened a streaming request to FastAPI.",
    });

    try {
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: currentSessionId,
        }),
        signal: abortController.signal,
      });

      if (!res.ok) {
        throw new Error(await res.text() || "Chat failed");
      }

      if (!res.body) {
        throw new Error("Chat stream was empty");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const agentMsg: Message = { role: "model", parts: [{ text: "" }] };
      setMessages((prev) => [...prev, agentMsg]);

      let buffer = "";
      let streamedText = "";

      const handleStreamEvent = (streamEvent: StreamEvent) => {
        if (requestIdRef.current !== requestId || abortController.signal.aborted) {
          return;
        }

        if (streamEvent.event === "session") {
          const sessionId = streamEvent.data.session_id;
          if (typeof sessionId === "string") {
            currentSessionId = sessionId;
            setActiveSessionId(sessionId);
          }

          if (streamEvent.data.created_session || !activeSessionId) {
            void fetchSessions();
          }
        }

        if (streamEvent.event === "status") {
          const label = streamEvent.data.label;
          addTraceEvent(requestId, {
            type: "status",
            title: typeof label === "string" ? label : "Working",
          });
        }

        if (streamEvent.event === "tool_call") {
          const name = streamEvent.data.name;
          const args = streamEvent.data.args;
          addTraceEvent(requestId, {
            type: "tool_call",
            title: typeof name === "string" ? `Calling ${name}` : "Calling tool",
            detail:
              args && typeof args === "object"
                ? JSON.stringify(args)
                : undefined,
          });
        }

        if (streamEvent.event === "tool_result") {
          const name = streamEvent.data.name;
          const summary = streamEvent.data.summary;
          addTraceEvent(requestId, {
            type: "tool_result",
            title:
              typeof name === "string"
                ? `${name} returned`
                : "Tool returned",
            detail:
              summary && typeof summary === "object"
                ? JSON.stringify(summary)
                : undefined,
          });
        }

        if (streamEvent.event === "chunk") {
          const chunk = streamEvent.data.text;
          if (typeof chunk !== "string") return;
          streamedText += chunk;

          setMessages((prev) =>
            prev.map((msg, idx) =>
              idx === prev.length - 1 && msg.role === "model"
                ? { ...msg, parts: [{ text: streamedText }] }
                : msg
            )
          );
        }

        if (streamEvent.event === "done") {
          addTraceEvent(requestId, {
            type: "done",
            title: "Response complete",
          });
          const messageCount = streamEvent.data.message_count;
          setSessions((prev) =>
            prev.map((s) =>
              s.session_id === currentSessionId
                ? {
                    ...s,
                    message_count:
                      typeof messageCount === "number"
                        ? messageCount
                        : s.message_count + 2,
                  }
                : s
            )
          );
        }

        if (streamEvent.event === "error") {
          const message = streamEvent.data.message;
          addTraceEvent(requestId, {
            type: "error",
            title: "Stream failed",
            detail: typeof message === "string" ? message : undefined,
          });
          throw new Error(typeof message === "string" ? message : "Chat stream failed");
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split(/\r?\n\r?\n/);
        buffer = events.pop() || "";

        for (const rawEvent of events) {
          const streamEvent = parseSseEvent(rawEvent);
          if (streamEvent) {
            handleStreamEvent(streamEvent);
          }
        }
      }

      buffer += decoder.decode();
      if (buffer.trim()) {
        const streamEvent = parseSseEvent(buffer);
        if (streamEvent) {
          handleStreamEvent(streamEvent);
        }
      }

      // Delightful micro-interaction: Pop confetti if page or database was created
      if (
        /created|success|added|sent/i.test(streamedText) &&
        /page|database|comment|email|event|calendar/i.test(streamedText)
      ) {
        confetti({
          particleCount: 100,
          spread: 70,
          origin: { y: 0.6 },
          colors: ["#8b5cf6", "#6366f1", "#a78bfa", "#c084fc"]
        });
      }

    } catch (err: unknown) {
      if (isAbortError(err)) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === "model" && !last.parts[0]?.text) {
            return prev.slice(0, -1);
          }
          return prev;
        });
        return;
      }
      console.error(err);
      setError("Failed to send message or receive response from Gemini.");
      // Rollback the optimistic user message and empty assistant placeholder.
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        const previous = prev[prev.length - 2];
        if (last?.role === "model" && previous?.role === "user") {
          return prev.slice(0, -2);
        }
        return prev.slice(0, -1);
      });
    } finally {
      if (requestIdRef.current === requestId) {
        abortControllerRef.current = null;
        setIsGenerating(false);
      }
    }
  };

  const handleStopGeneration = () => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    requestIdRef.current += 1;
    setIsGenerating(false);
  };

  // Initial load and periodic polling
  useEffect(() => {
    const bootTimer = window.setTimeout(() => {
      void fetchSessions();
      void checkHealth();
    }, 0);

    // Poll server health every 8 seconds
    const interval = setInterval(checkHealth, 8000);
    return () => {
      window.clearTimeout(bootTimer);
      clearInterval(interval);
      abortControllerRef.current?.abort();
    };
  }, []);

  return (
    <div className="flex h-screen w-screen bg-[#060608] text-[#f4f4f7] overflow-hidden">
      {/* Sidebar (Session management) */}
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={loadSession}
        onCreateSession={handleCreateSession}
        onDeleteSession={handleDeleteSession}
        healthStatus={healthStatus}
      />

      {/* Main Work Area */}
      <main className="flex-1 flex flex-col h-full overflow-hidden relative">
        {/* Error Header Alert */}
        {error && (
          <div className="bg-rose-500/10 border-b border-rose-500/20 px-6 py-3 flex items-center justify-between text-sm text-rose-400 z-20">
            <div className="flex items-center space-x-2">
              <AlertCircle className="w-4.5 h-4.5 shrink-0" />
              <span>{error}</span>
            </div>
            <button
              onClick={() => { fetchSessions(); checkHealth(); }}
              className="p-1 rounded-lg hover:bg-white/5 text-gray-400 hover:text-white cursor-pointer"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Chat Window */}
        <ChatContainer
          messages={messages}
          isGenerating={isGenerating}
          onSendMessage={handleSendMessage}
          onStopGeneration={handleStopGeneration}
          activeSessionId={activeSessionId}
        />
      </main>

      {/* Right Drawer (Execution Flow Trace) */}
      <ExecutionTrace
        isGenerating={isGenerating}
        healthStatus={healthStatus}
        traceEvents={traceEvents}
      />
    </div>
  );
}
