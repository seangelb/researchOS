import type { Position } from "../types";
import { formatCurrency, formatCurrencyPrecise, formatPct, gainClass } from "../utils";

interface Props {
  positions: Position[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
}

export function PositionsTable({ positions, selectedId, onSelect, onDelete }: Props) {
  if (positions.length === 0) {
    return (
      <div className="card empty">
        <p>No positions yet. Add one to start building your research workspace.</p>
      </div>
    );
  }
  return (
    <div className="card table-wrap">
      <table className="positions">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Sector</th>
            <th className="num">Price</th>
            <th className="num">Mkt Value</th>
            <th className="num">P/L</th>
            <th className="num">Return</th>
            <th className="num">Upside</th>
            <th>Conviction</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr
              key={p.id}
              className={p.id === selectedId ? "selected" : ""}
              onClick={() => onSelect(p.id)}
            >
              <td>
                <span className="symbol">{p.symbol}</span>
                <span className="company">{p.name}</span>
              </td>
              <td>{p.sector}</td>
              <td className="num">{formatCurrencyPrecise(p.current_price)}</td>
              <td className="num">{formatCurrency(p.market_value)}</td>
              <td className={`num ${gainClass(p.unrealized_gain)}`}>
                {formatCurrency(p.unrealized_gain)}
              </td>
              <td className={`num ${gainClass(p.unrealized_gain_pct)}`}>
                {formatPct(p.unrealized_gain_pct)}
              </td>
              <td className={`num ${gainClass(p.upside_pct)}`}>{formatPct(p.upside_pct)}</td>
              <td>
                <span className={`badge conviction-${p.conviction}`}>{p.conviction}</span>
              </td>
              <td>
                <button
                  className="link-danger"
                  aria-label={`delete ${p.symbol}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(p.id);
                  }}
                >
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
