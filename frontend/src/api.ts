import type { NewPosition, Note, PortfolioSummary, Position } from "./types";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${detail}`);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  listPositions: () => request<Position[]>("/api/positions"),
  createPosition: (payload: NewPosition) =>
    request<Position>("/api/positions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updatePosition: (id: number, payload: Partial<NewPosition>) =>
    request<Position>(`/api/positions/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deletePosition: (id: number) =>
    request<void>(`/api/positions/${id}`, { method: "DELETE" }),
  listNotes: (positionId: number) =>
    request<Note[]>(`/api/positions/${positionId}/notes`),
  createNote: (positionId: number, payload: { title: string; content: string }) =>
    request<Note>(`/api/positions/${positionId}/notes`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteNote: (id: number) => request<void>(`/api/notes/${id}`, { method: "DELETE" }),
  summary: () => request<PortfolioSummary>("/api/portfolio/summary"),
};
