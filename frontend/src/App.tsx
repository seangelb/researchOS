import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { AddPositionForm } from "./components/AddPositionForm";
import { NotesPanel } from "./components/NotesPanel";
import { PositionsTable } from "./components/PositionsTable";
import { SummaryCards } from "./components/SummaryCards";
import type { NewPosition, PortfolioSummary, Position } from "./types";

export default function App() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [pos, sum] = await Promise.all([api.listPositions(), api.summary()]);
      setPositions(pos);
      setSummary(sum);
      setError(null);
      setSelectedId((current) => {
        if (current && pos.some((p) => p.id === current)) return current;
        return pos.length > 0 ? pos[0].id : null;
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createPosition = useCallback(
    async (payload: NewPosition) => {
      const created = await api.createPosition(payload);
      await refresh();
      setSelectedId(created.id);
    },
    [refresh],
  );

  const deletePosition = useCallback(
    async (id: number) => {
      await api.deletePosition(id);
      await refresh();
    },
    [refresh],
  );

  const selected = useMemo(
    () => positions.find((p) => p.id === selectedId) ?? null,
    [positions, selectedId],
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◎</span>
          <div>
            <h1>ResearchOS</h1>
            <p>Personal investment research workspace</p>
          </div>
        </div>
        <a className="api-link" href="/api/health" target="_blank" rel="noreferrer">
          API status
        </a>
      </header>

      {error && <div className="banner error">Could not reach API: {error}</div>}

      <SummaryCards summary={summary} />

      <main className="layout">
        <div className="left-col">
          <AddPositionForm onCreate={createPosition} />
          {loading ? (
            <div className="card empty">Loading positions…</div>
          ) : (
            <PositionsTable
              positions={positions}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onDelete={deletePosition}
            />
          )}
        </div>
        <NotesPanel position={selected} />
      </main>
    </div>
  );
}
