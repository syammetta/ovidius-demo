import { useCallback, useEffect, useRef, useState } from "react";
import type { ConnectionStatus, WSEvent, QueryLogEntry } from "./types";

function wsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/qa`;
}

export function useQASocket(onEvent: (event: WSEvent) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    setStatus("connecting");
    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;

    ws.onopen = () => setStatus("connected");

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as WSEvent;
        onEventRef.current(data);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onerror = () => setStatus("error");

    ws.onclose = () => {
      setStatus("disconnected");
      wsRef.current = null;
    };
  }, []);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setStatus("disconnected");
  }, []);

  const send = useCallback(
    (question: string, mode: "direct" | "agent" = "direct") => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      wsRef.current.send(JSON.stringify({ question, mode }));
    },
    [],
  );

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  return { status, connect, disconnect, send };
}

export async function fetchQueryLogs(limit = 20): Promise<QueryLogEntry[]> {
  const res = await fetch(`/query-logs?limit=${limit}`);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchHealth(): Promise<{
  status: string;
  child_chunks: number;
  parent_chunks: number;
} | null> {
  try {
    const res = await fetch("/health");
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}
