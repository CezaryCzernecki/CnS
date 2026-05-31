"use client";

import { useState, FormEvent } from "react";
import { Sparkles, Loader2, AlertTriangle, TrendingUp } from "lucide-react";
import { fetchPrediction, fetchBaselinePrediction, type PredictionResponse } from "@/lib/api";

export default function PredictPage() {
  const [stationId, setStationId] = useState("33506");
  const [departure, setDeparture] = useState(() => {
    const d = new Date();
    d.setMinutes(0, 0, 0);
    d.setHours(d.getHours() + 1);
    return d.toISOString().slice(0, 16);
  });
  const [prevDelay, setPrevDelay] = useState("0");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ xgb: PredictionResponse; baseline: PredictionResponse } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const [xgb, baseline] = await Promise.all([
        fetchPrediction(stationId, departure + ":00", Number(prevDelay)),
        fetchBaselinePrediction(stationId, departure + ":00"),
      ]);
      setResult({ xgb, baseline });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Błąd predykcji");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">Predykcja opóźnienia</h1>
        <p className="text-sm text-zinc-500">
          Model XGBoost + baseline (mediana historyczna)
        </p>
      </div>

      {/* Form */}
      <form
        onSubmit={handleSubmit}
        className="rounded-lg border border-zinc-200 bg-white p-5 shadow-sm space-y-4"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="ID stacji PKP"
            hint="np. 33506 (Warszawa Centralna)"
          >
            <input
              type="text"
              value={stationId}
              onChange={(e) => setStationId(e.target.value)}
              className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="33506"
              required
            />
          </Field>

          <Field label="Planowany odjazd">
            <input
              type="datetime-local"
              value={departure}
              onChange={(e) => setDeparture(e.target.value)}
              className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </Field>

          <Field
            label="Opóźnienie poprzedniego przystanku"
            hint="0 jeśli nieznane"
          >
            <input
              type="number"
              value={prevDelay}
              onChange={(e) => setPrevDelay(e.target.value)}
              className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              min="0"
              step="1"
            />
          </Field>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="flex items-center gap-2 rounded-md bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
          Oblicz predykcję
        </button>
      </form>

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <AlertTriangle className="h-4 w-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-4">
          {/* XGB */}
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-5 space-y-4">
            <div className="flex items-center gap-2">
              <TrendingUp className="h-5 w-5 text-blue-600" />
              <h2 className="font-semibold text-blue-900">XGBoost</h2>
              <span className="ml-auto text-xs text-blue-400">{result.xgb.model_date}</span>
            </div>

            <div className="flex gap-6">
              <Metric label="Predykcja" value={`${result.xgb.predicted_delay_min?.toFixed(1)} min`} big />
              {result.xgb.p75_delay_min != null && (
                <Metric label="P75" value={`${result.xgb.p75_delay_min.toFixed(1)} min`} />
              )}
              {result.xgb.confidence_interval && (
                <Metric
                  label="Przedział 70% CI"
                  value={`${result.xgb.confidence_interval[0].toFixed(1)} – ${result.xgb.confidence_interval[1].toFixed(1)} min`}
                />
              )}
            </div>

            {result.xgb.explanation && result.xgb.explanation.length > 0 && (
              <div>
                <p className="text-xs font-medium text-blue-600 mb-2">
                  Wyjaśnienie SHAP (top-5 cech)
                </p>
                <div className="space-y-1">
                  {result.xgb.explanation.map((item) => (
                    <div key={item.feature} className="flex items-center gap-2 text-sm">
                      <span className="w-40 truncate text-xs text-zinc-600">{item.feature}</span>
                      <div className="flex-1 flex items-center gap-1">
                        <div
                          className={`h-3 rounded-sm ${item.impact >= 0 ? "bg-orange-400" : "bg-blue-400"}`}
                          style={{
                            width: `${Math.min(100, Math.abs(item.impact) * 8)}%`,
                            minWidth: "2px",
                          }}
                        />
                        <span
                          className={`text-xs font-medium ${item.impact >= 0 ? "text-orange-700" : "text-blue-700"}`}
                        >
                          {item.impact >= 0 ? "+" : ""}
                          {item.impact.toFixed(2)} min
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Baseline */}
          <div className="rounded-lg border border-zinc-200 bg-white p-5 space-y-3">
            <div className="flex items-center gap-2">
              <h2 className="font-semibold text-zinc-700">Baseline (mediana historyczna)</h2>
            </div>
            <div className="flex gap-6">
              <Metric label="Predykcja" value={`${(result.baseline.predicted_delay_min ?? 0).toFixed(1)} min`} />
              {result.baseline.p75_delay_min != null && (
                <Metric label="P75" value={`${result.baseline.p75_delay_min.toFixed(1)} min`} />
              )}
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

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

function Metric({ label, value, big = false }: { label: string; value: string; big?: boolean }) {
  return (
    <div>
      <p className="text-xs text-zinc-500">{label}</p>
      <p className={`font-bold text-zinc-900 ${big ? "text-2xl" : "text-lg"}`}>{value}</p>
    </div>
  );
}
