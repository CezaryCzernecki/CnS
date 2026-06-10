const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Typy – mapowanie z cns/api/app.py
// ---------------------------------------------------------------------------

export interface ActiveDelay {
  schedule_id: number;
  order_id: number;
  operating_date: string | null;
  train_status: string | null;
  snapshot_time: string | null;
  train_number: string | null;
  train_name: string | null;
  carrier_name: string | null;
  first_station: string | null;
  first_station_departure: string | null;
  last_station: string | null;
  last_station_arrival: string | null;
  last_visited_station: string | null;
  last_visited_arrival: string | null;
  delay_departure_min: number | null;
  delay_arrival_min: number | null;
}

export interface StationStat {
  station_id: number | null;
  station_name: string | null;
  total_stops: number;
  stops_with_data: number;
  delayed_count: number;
  avg_delay_min: number | null;
  max_delay_min: number | null;
  delay_rate_pct: number | null;
}

export interface StationMapPoint {
  station_id: number | null;
  station_name: string | null;
  latitude: number | null;
  longitude: number | null;
  avg_delay_min: number | null;
  delay_rate_pct: number | null;
  total_stops: number;
}

export interface ExplanationItem {
  feature: string;
  impact: number;
  value: unknown;
}

export interface PredictionResponse {
  station_id: string;
  station_name: string | null;
  predicted_delay_min: number;
  p75_delay_min: number | null;
  confidence_interval: [number, number] | null;
  model: string;
  model_date: string | null;
  explanation: ExplanationItem[] | null;
}

export interface DbStats {
  stations: number;
  carriers: number;
  snapshots: number;
  train_ops: number;
  stops: number;
  disruptions: number;
  last_snapshot: string | null;
  measurement_start: string | null;
}

export interface AllTimeRankingEntry {
  train_number: string | null;
  train_name: string | null;
  carrier_name: string | null;
  operating_date: string | null;
  max_delay_min: number | null;
  first_station: string | null;
  last_station: string | null;
  has_bus_replacement: boolean;
  bus_segment: string | null;
}

export interface DailyRankingEntry {
  train_number: string | null;
  train_name: string | null;
  carrier_name: string | null;
  max_delay_min: number | null;
  first_station: string | null;
  last_station: string | null;
}

export interface MonthlyTrainRankingEntry {
  train_number: string | null;
  train_name: string | null;
  carrier_name: string | null;
  first_station: string | null;
  last_station: string | null;
  trip_count: number;
  total_delay_min: number | null;
  avg_delay_min: number | null;
}

export interface MonthlyCarrierRankingEntry {
  carrier_name: string | null;
  trip_count: number;
  total_delay_min: number | null;
  avg_delay_min: number | null;
  cancelled_count: number;
}

// ---------------------------------------------------------------------------
// Fetch helper
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(
  path: string,
  params?: Record<string, string | number>,
  options?: RequestInit
): Promise<T> {
  const url = new URL(`${BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  }
  const res = await fetch(url.toString(), {
    next: { revalidate: 0 },
    ...options,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // ignore parse errors
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Publiczne API
// ---------------------------------------------------------------------------

export const fetchActiveDelays = (limit = 500): Promise<ActiveDelay[]> =>
  apiFetch("/delays/active", { limit });

export const fetchTopStations = (limit = 20): Promise<StationStat[]> =>
  apiFetch("/delays/stations/top", { limit });

export const fetchStats = (): Promise<DbStats> => apiFetch("/stats");

export const fetchMapStations = (limit = 60): Promise<StationMapPoint[]> =>
  apiFetch("/delays/stations/map", { limit });

export const fetchPrediction = (
  stationId: string,
  departure: string,
  prevDelay = 0
): Promise<PredictionResponse> =>
  apiFetch("/predict", {
    station_id: stationId,
    planned_departure: departure,
    prev_stop_delay_min: prevDelay,
  });

export const fetchBaselinePrediction = (
  stationId: string,
  departure: string
): Promise<PredictionResponse> =>
  apiFetch("/predict/baseline", {
    station_id: stationId,
    planned_departure: departure,
  });

export const fetchRankingsAllTime = (limit = 10): Promise<AllTimeRankingEntry[]> =>
  apiFetch("/rankings/all-time", { limit });

export const fetchRankingsDaily = (date: string, limit = 10): Promise<DailyRankingEntry[]> =>
  apiFetch("/rankings/daily", { date, limit });

export const fetchRankingsMonthlyTrains = (
  year: number,
  month: number,
  limit = 10
): Promise<MonthlyTrainRankingEntry[]> =>
  apiFetch("/rankings/monthly/trains", { year, month, limit });

export const fetchRankingsMonthlyCarriers = (
  year: number,
  month: number
): Promise<MonthlyCarrierRankingEntry[]> =>
  apiFetch("/rankings/monthly/carriers", { year, month });
