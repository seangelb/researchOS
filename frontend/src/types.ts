export type Conviction = "low" | "medium" | "high";

export interface Position {
  id: number;
  symbol: string;
  name: string;
  sector: string;
  shares: number;
  cost_basis: number;
  current_price: number;
  target_price: number;
  conviction: Conviction;
  thesis: string;
  created_at: string;
  market_value: number;
  total_cost: number;
  unrealized_gain: number;
  unrealized_gain_pct: number;
  upside_pct: number;
}

export interface Note {
  id: number;
  position_id: number;
  title: string;
  content: string;
  created_at: string;
}

export interface PortfolioSummary {
  positions: number;
  market_value: number;
  total_cost: number;
  unrealized_gain: number;
  unrealized_gain_pct: number;
  by_sector: Record<string, number>;
}

export interface NewPosition {
  symbol: string;
  name: string;
  sector: string;
  shares: number;
  cost_basis: number;
  current_price: number;
  target_price: number;
  conviction: Conviction;
  thesis: string;
}
