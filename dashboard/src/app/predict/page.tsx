"use client";

import { useEffect, useState, useCallback, FormEvent, useId } from "react";
import { format } from "date-fns";
import {
  Sparkles,
  Loader2,
  Search,
  Clock,
  TrendingUp,
  History,
  ChevronRight,
} from "lucide-react";
import {
  fetchTopStations,
  fetchPrediction,
  type StationStat,
  type PredictionResponse,
  ApiError,
} from "@/lib/api";
import { DelayBadge } from "@/components/DelayBadge";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { ErrorBanner } from "@/components/ErrorBanner";

// ---------------------------------------------------------------------------
// SHAP feature labels + emoji
// ---------------------------------------------------------------------------

const FEATURE_META: Record<string, { label: string; emoji: string }> = {
  prev_stop_delay_min:  { label: "Poprzednie opóźnienie",   emoji: "🔴" },
  station_id:           { label: "Stacja",                   emoji: "📍" },
  day_type:             { label: "Typ dnia",                 emoji: "📅" },
  hour_of_day:          { label: "Pora dnia",                emoji: "🕐" },
  day_of_week:          { label: "Dzień tygodnia",           emoji: "📆" },
  month:                { label: "Miesiąc",                  emoji: "📆" },
  planned_sequence:     { label: "Nr przystanku na trasie",  emoji: "🚉" },
  snowfall_cm:          { label: "Opady śniegu",             emoji: "❄️" },
  precipitation_mm:     { label: "Opady deszczu",            emoji: "🌧️" },
  wind_speed_kmh:       { label: "Prędkość wiatru",          emoji: "💨" },
  temperature_c:        { label: "Temperatura",              emoji: "🌡️" },
  visibility_m:         { label: "Widzialność",              emoji: "🌫️" },
  cloud_cover_pct:      { label: "Zachmurzenie",             emoji: "☁️" },
  is_snowing:           { label: "Śnieg",                   emoji: "❄️" },
  is_heavy_rain:        { label: "Ulewny deszcz",            emoji: "⛈️" },
  is_strong_wind:       { label: "Silny wiatr",              emoji: "🌬️" },
  is_frost:             { label: "Mróz",                    emoji: "🧊" },
  is_dense_fog:         { label: "Gęsta mgła",              emoji: "🌫️" },
};

// ---------------------------------------------------------------------------
// Typy
// ---------------------------------------------------------------------------

interface HistoryItem {
  id: string;
  timestamp: string;
  station_id: string;
  station_name: string | null;
  departure: string;
  prev_delay: number;
  predicted_min: number;
  model: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const INPUT_CLS =
  "w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm " +
  "placeholder:text-zinc-400 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400";

function defaultDeparture(): string {
  const d = new Date();
  d.setMinutes(0, 0, 0);
  d.setHours(d.getHours() + 1);
  return d.toISOString().slice(0, 16);
}

function predColor(min: number): string {
  if (min < 5) return "text-green-600";
  if (min <= 15) return "text-yellow-600";
  return "text-red-600";
}

// ---------------------------------------------------------------------------
// Strona
// ---------------------------------------------------------------------------

export default function PredictPage() {
  const listId = useId();

  // Formularz
  const [stationQuery, setStationQuery] = useState("");
  const [stationId, setStationId] = useState("33506");
  const [departure, setDeparture] = useState(defaultDeparture);
  const [prevDelay, setPrevDelay] = useState(0);

  // Dane stacji do autocomplete
  const [stations, setStations] = useState<StationStat[]>([]);
  const [stationsLoading, setStationsLoading] = useState(true);

  // Wynik predykcji
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PredictionResponse | null>(null);
  const [resultTime, setResultTime] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Historia (localStorage)
  const [history, setHistory] = useState<HistoryItem[]>([]);

  // ---------------------------------------------------------------------------
  // Ładowanie stacji + historia z localStorage
  // ---------------------------------------------------------------------------

  useEffect(() => {
    fetchTopStations(200)
      .then(setStations)
      .catch(() => {})
      .finally(() => setStationsLoading(false));
  }, []);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("cns_predict_history");
      if (raw) setHistory(JSON.parse(raw));
    } catch {}
  }, []);

  const saveHistory = useCallback((items: HistoryItem[]) => {
    setHistory(items);
    try {
      localStorage.setItem("cns_predict_history", JSON.stringify(items));
    } catch {}
  }, []);

  // ---------------------------------------------------------------------------
  // Autocomplete – mapowanie nazwy → ID
  // ---------------------------------------------------------------------------

  const handleStationInput = (value: string) => {
    setStationQuery(value);
    const match = stations.find(
      (s) => s.station_name === value || String(s.station_id) === value
    );
    setStationId(match ? String(match.station_id) : value.replace(/\D/g, "") || value);
  };

  // ---------------------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------------------

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!stationId) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setResultTime(null);

    const departureISO = departure.length === 16 ? departure + ":00" : departure;

    try {
      const data = await fetchPrediction(stationId, departureISO, prevDelay);
      setResult(data);
      const now = new Date();
      setResultTime(now);

      // Dodaj do historii
      const item: HistoryItem = {
        id: `${Date.now()}`,
        timestamp: now.toISOString(),
        station_id: stationId,
        station_name: data.station_name ?? (stationQuery || null),
        departure,
        prev_delay: prevDelay,
        predicted_min: data.predicted_delay_min,
        model: data.model,
      };
      saveHistory([item, ...history].slice(0, 5));
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.status === 503
            ? `Model niedostępny: ${err.message}`
            : err.message
          : err instanceof Error
            ? err.message
            : "Błąd predykcji";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  // ---------------------------------------------------------------------------
  // JSX
  // ---------------------------------------------------------------------------

  const stationName =
    result?.station_name ??
    stations.find((s) => String(s.station_id) === stationId)?.station_name ??
    (stationQuery || stationId);

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      {/* Nagłówek */}
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">Predykcja opóźnienia</h1>
        <p className="text-sm text-zinc-500">
          Model XGBoost · wyjaśnienia SHAP · dane pogodowe z bazy
        </p>
      </div>

      {/* Formularz */}
      <form
        onSubmit={handleSubmit}
        className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm space-y-4"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {/* Stacja */}
          <div className="sm:col-span-2">
            <label className="mb-1 block text-sm font-medium text-zinc-700">
              Stacja PKP
            </label>
            <div className="relative">
              {stationsLoading ? (
                <div className="absolute left-3 top-1/2 -translate-y-1/2">
                  <LoadingSpinner size="sm" />
                </div>
              ) : (
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
              )}
              <input
                list={listId}
                value={stationQuery}
                onChange={(e) => handleStationInput(e.target.value)}
                placeholder="Wpisz nazwę stacji lub ID…"
                className={`${INPUT_CLS} pl-9`}
                required
                autoComplete="off"
              />
              <datalist id={listId}>
                {stations.map((s) => (
                  <option
                    key={s.station_id}
                    value={s.station_name ?? String(s.station_id)}
                  />
                ))}
              </datalist>
            </div>
            {stationId && stationQuery && (
              <p className="mt-1 text-xs text-zinc-400">
                ID: {stationId}
              </p>
            )}
          </div>

          {/* Odjazd */}
          <Field label="Planowany odjazd">
            <input
              type="datetime-local"
              value={departure}
              onChange={(e) => setDeparture(e.target.value)}
              className={INPUT_CLS}
              required
            />
          </Field>

          {/* Poprzednie opóźnienie */}
          <Field
            label="Opóźnienie poprzedniego przystanku"
            hint="0 jeśli nieznane lub pierwszy przystanek"
          >
            <input
              type="number"
              value={prevDelay}
              onChange={(e) => setPrevDelay(Number(e.target.value))}
              className={INPUT_CLS}
              min="0"
              max="300"
              step="1"
            />
          </Field>
        </div>

        <button
          type="submit"
          disabled={loading || !stationId}
          className="flex items-center gap-2 rounded-md bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
          Przewiduj opóźnienie
        </button>
      </form>

      {/* Błąd */}
      {error && <ErrorBanner message={error} onRetry={() => setError(null)} />}

      {/* Wynik */}
      {result && (
        <PredictionResult
          result={result}
          stationName={stationName}
          resultTime={resultTime}
        />
      )}

      {/* Historia */}
      {history.length > 0 && <PredictionHistory history={history} onClear={() => saveHistory([])} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Wynik predykcji
// ---------------------------------------------------------------------------

function PredictionResult({
  result,
  stationName,
  resultTime,
}: {
  result: PredictionResponse;
  stationName: string;
  resultTime: Date | null;
}) {
  const pred = result.predicted_delay_min;
  const p75 = result.p75_delay_min;
  const ci = result.confidence_interval;

  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50 p-5 space-y-5">
      {/* Nagłówek */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-medium text-blue-400 uppercase tracking-wide">
            {result.model === "baseline_fallback" ? "Baseline (XGB niedostępny)" : "XGBoost"}
            {result.model_date && ` · model z ${result.model_date}`}
          </p>
          <p className="font-semibold text-blue-900 text-sm mt-0.5">{stationName}</p>
        </div>
        {resultTime && (
          <span className="flex items-center gap-1 text-xs text-blue-300">
            <Clock className="h-3 w-3" />
            prognoza z {format(resultTime, "HH:mm")}
          </span>
        )}
      </div>

      {/* Główna liczba */}
      <div className="flex items-end gap-4">
        <div>
          <p className="text-xs text-blue-500 mb-0.5">Przewidywane opóźnienie</p>
          <p className={`text-5xl font-black leading-none ${predColor(pred)}`}>
            {pred >= 0 ? "+" : ""}
            {pred.toFixed(0)}
            <span className="text-xl font-medium text-zinc-400 ml-1">min</span>
          </p>
        </div>
        <DelayBadge delay={Math.round(pred)} size="md" />
      </div>

      {/* Pasek percentyli */}
      {(p75 !== null || ci !== null) && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-blue-600">Rozkład prawdopodobieństwa</p>

          {ci && (
            <PercentileBar
              label="Przedział 70% (CI)"
              low={ci[0]}
              high={ci[1]}
              color="bg-blue-200"
            />
          )}

          <div className="flex gap-4 text-xs text-zinc-600">
            <span>
              <span className="font-semibold text-zinc-800">50%</span> szansa na mniej niż{" "}
              <span className="font-bold text-zinc-900">{pred.toFixed(1)}</span> min
            </span>
            {p75 !== null && (
              <span>
                <span className="font-semibold text-zinc-800">75%</span> szansa na mniej niż{" "}
                <span className="font-bold text-zinc-900">{p75.toFixed(1)}</span> min
              </span>
            )}
          </div>
        </div>
      )}

      {/* SHAP wyjaśnienia */}
      {result.explanation && result.explanation.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-blue-600 flex items-center gap-1">
            <TrendingUp className="h-3.5 w-3.5" />
            Czynniki wpływające na predykcję (SHAP)
          </p>
          <div className="space-y-1.5">
            {result.explanation.map((item, i) => {
              const meta = FEATURE_META[item.feature] ?? {
                label: item.feature,
                emoji: "•",
              };
              const isPositive = item.impact >= 0;
              const maxAbs = Math.max(
                ...result.explanation!.map((x) => Math.abs(x.impact))
              );
              const widthPct = maxAbs > 0 ? (Math.abs(item.impact) / maxAbs) * 100 : 0;

              return (
                <div key={i} className="flex items-center gap-2 text-sm">
                  <span className="text-base w-5 flex-shrink-0 text-center">{meta.emoji}</span>
                  <span className="w-40 flex-shrink-0 truncate text-xs text-zinc-600">
                    {meta.label}
                  </span>
                  <div className="flex flex-1 items-center gap-1.5">
                    <div
                      className={`h-3 rounded-sm transition-all ${
                        isPositive ? "bg-orange-400" : "bg-blue-400"
                      }`}
                      style={{ width: `${widthPct}%`, minWidth: "2px" }}
                    />
                    <span
                      className={`text-xs font-semibold ${
                        isPositive ? "text-orange-700" : "text-blue-700"
                      }`}
                    >
                      {isPositive ? "+" : ""}
                      {item.impact.toFixed(2)} min
                    </span>
                    {item.value !== null && item.value !== undefined && (
                      <span className="text-xs text-zinc-400">
                        (wartość: {String(item.value)})
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <p className="text-xs text-zinc-400 mt-1">
            Wartości SHAP sumują się do predykcji. Czerwony = zwiększa opóźnienie, niebieski = zmniejsza.
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pasek percentylowy
// ---------------------------------------------------------------------------

function PercentileBar({
  label,
  low,
  high,
  color,
}: {
  label: string;
  low: number;
  high: number;
  color: string;
}) {
  const maxRange = 60;
  const clampedLow = Math.max(0, low);
  const clampedHigh = Math.min(maxRange, high);

  return (
    <div className="flex items-center gap-3">
      <span className="w-28 flex-shrink-0 text-xs text-zinc-500">{label}</span>
      <div className="relative flex-1 h-4 bg-white/60 rounded-full overflow-hidden border border-blue-100">
        <div
          className={`absolute h-full rounded-full ${color}`}
          style={{
            left: `${(clampedLow / maxRange) * 100}%`,
            width: `${((clampedHigh - clampedLow) / maxRange) * 100}%`,
          }}
        />
      </div>
      <span className="w-24 flex-shrink-0 text-xs text-zinc-500 text-right">
        {low.toFixed(1)} – {high.toFixed(1)} min
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Historia predykcji
// ---------------------------------------------------------------------------

function PredictionHistory({
  history,
  onClear,
}: {
  history: HistoryItem[];
  onClear: () => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-zinc-700">
          <History className="h-4 w-4" />
          Ostatnie predykcje
        </h2>
        <button
          onClick={onClear}
          className="text-xs text-zinc-400 hover:text-zinc-600"
        >
          Wyczyść
        </button>
      </div>

      <div className="space-y-1.5">
        {history.map((item) => (
          <div
            key={item.id}
            className="flex items-center gap-3 rounded-lg border border-zinc-100 bg-white px-3 py-2.5 text-sm shadow-sm"
          >
            <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-zinc-300" />
            <div className="flex-1 min-w-0">
              <p className="font-medium text-zinc-800 truncate">
                {item.station_name ?? item.station_id}
              </p>
              <p className="text-xs text-zinc-400">
                {item.departure.slice(0, 16).replace("T", " ")}
                {item.prev_delay > 0 && ` · poprzednie +${item.prev_delay} min`}
              </p>
            </div>
            <div className="flex flex-col items-end gap-0.5">
              <DelayBadge delay={Math.round(item.predicted_min)} size="sm" />
              <span className="text-xs text-zinc-400">
                {format(new Date(item.timestamp), "HH:mm")}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers UI
// ---------------------------------------------------------------------------

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-zinc-700">{label}</label>
      {children}
      {hint && <p className="text-xs text-zinc-400">{hint}</p>}
    </div>
  );
}
