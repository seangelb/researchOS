import { useState } from "react";
import type { Conviction, NewPosition } from "../types";

interface Props {
  onCreate: (payload: NewPosition) => Promise<void>;
}

const EMPTY: NewPosition = {
  symbol: "",
  name: "",
  sector: "Technology",
  shares: 0,
  cost_basis: 0,
  current_price: 0,
  target_price: 0,
  conviction: "medium",
  thesis: "",
};

export function AddPositionForm({ onCreate }: Props) {
  const [form, setForm] = useState<NewPosition>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update<K extends keyof NewPosition>(key: K, value: NewPosition[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!form.symbol.trim() || !form.name.trim()) {
      setError("Symbol and name are required.");
      return;
    }
    setBusy(true);
    try {
      await onCreate(form);
      setForm(EMPTY);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card add-form" onSubmit={submit}>
      <h2>Add position</h2>
      <div className="form-grid">
        <label>
          Symbol
          <input
            aria-label="symbol"
            value={form.symbol}
            onChange={(e) => update("symbol", e.target.value)}
            placeholder="TSLA"
          />
        </label>
        <label>
          Name
          <input
            aria-label="name"
            value={form.name}
            onChange={(e) => update("name", e.target.value)}
            placeholder="Tesla Inc."
          />
        </label>
        <label>
          Sector
          <input
            aria-label="sector"
            value={form.sector}
            onChange={(e) => update("sector", e.target.value)}
          />
        </label>
        <label>
          Conviction
          <select
            aria-label="conviction"
            value={form.conviction}
            onChange={(e) => update("conviction", e.target.value as Conviction)}
          >
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </label>
        <label>
          Shares
          <input
            aria-label="shares"
            type="number"
            step="any"
            value={form.shares}
            onChange={(e) => update("shares", Number(e.target.value))}
          />
        </label>
        <label>
          Cost basis
          <input
            aria-label="cost_basis"
            type="number"
            step="any"
            value={form.cost_basis}
            onChange={(e) => update("cost_basis", Number(e.target.value))}
          />
        </label>
        <label>
          Current price
          <input
            aria-label="current_price"
            type="number"
            step="any"
            value={form.current_price}
            onChange={(e) => update("current_price", Number(e.target.value))}
          />
        </label>
        <label>
          Target price
          <input
            aria-label="target_price"
            type="number"
            step="any"
            value={form.target_price}
            onChange={(e) => update("target_price", Number(e.target.value))}
          />
        </label>
      </div>
      <label className="full">
        Thesis
        <textarea
          aria-label="thesis"
          value={form.thesis}
          onChange={(e) => update("thesis", e.target.value)}
          placeholder="Why you own it..."
          rows={2}
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? "Adding..." : "Add to workspace"}
      </button>
    </form>
  );
}
