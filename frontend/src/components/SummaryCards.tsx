import type { PortfolioSummary } from "../types";
import { formatCurrency, formatPct, gainClass } from "../utils";

interface Props {
  summary: PortfolioSummary | null;
}

export function SummaryCards({ summary }: Props) {
  if (!summary) return null;
  return (
    <section className="summary-grid">
      <div className="card stat">
        <span className="stat-label">Market Value</span>
        <span className="stat-value">{formatCurrency(summary.market_value)}</span>
      </div>
      <div className="card stat">
        <span className="stat-label">Total Cost</span>
        <span className="stat-value">{formatCurrency(summary.total_cost)}</span>
      </div>
      <div className="card stat">
        <span className="stat-label">Unrealized P/L</span>
        <span className={`stat-value ${gainClass(summary.unrealized_gain)}`}>
          {formatCurrency(summary.unrealized_gain)}
        </span>
      </div>
      <div className="card stat">
        <span className="stat-label">Return</span>
        <span className={`stat-value ${gainClass(summary.unrealized_gain_pct)}`}>
          {formatPct(summary.unrealized_gain_pct)}
        </span>
      </div>
      <div className="card stat">
        <span className="stat-label">Positions</span>
        <span className="stat-value">{summary.positions}</span>
      </div>
    </section>
  );
}
