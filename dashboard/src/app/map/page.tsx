"use client";

import { useEffect, useState } from "react";
import { fetchMapStations, type StationMapPoint } from "@/lib/api";
import { MapPin, Loader2 } from "lucide-react";

function delayColorHex(avg: number | null): string {
  if (avg === null) return "#94a3b8";
  if (avg <= 2) return "#22c55e";
  if (avg <= 5) return "#eab308";
  if (avg <= 15) return "#f97316";
  return "#ef4444";
}

export default function MapPage() {
  const [stations, setStations] = useState<StationMapPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<StationMapPoint | null>(null);

  useEffect(() => {
    fetchMapStations(60)
      .then(setStations)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const withCoords = stations.filter((s) => s.latitude && s.longitude);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">Mapa opóźnień</h1>
        <p className="text-sm text-zinc-500">Średnie opóźnienia wg stacji (ostatnie 7 dni)</p>
      </div>

      {loading ? (
        <div className="flex h-96 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
        </div>
      ) : (
        <div className="flex gap-4">
          {/* Station list – placeholder for map */}
          <div className="flex-1 rounded-lg border border-zinc-200 bg-white shadow-sm">
            <div className="border-b border-zinc-200 bg-zinc-50 px-4 py-3">
              <h2 className="font-medium text-zinc-700">
                Stacje z koordynatami ({withCoords.length})
              </h2>
              <p className="text-xs text-zinc-400 mt-0.5">
                Integracja MapLibre GL – podpięta w kolejnej iteracji
              </p>
            </div>

            {/* Legend */}
            <div className="flex gap-3 px-4 py-2 text-xs text-zinc-500 border-b border-zinc-100">
              {[
                { color: "#22c55e", label: "≤2 min" },
                { color: "#eab308", label: "≤5 min" },
                { color: "#f97316", label: "≤15 min" },
                { color: "#ef4444", label: ">15 min" },
              ].map(({ color, label }) => (
                <span key={label} className="flex items-center gap-1">
                  <span
                    className="inline-block h-3 w-3 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  {label}
                </span>
              ))}
            </div>

            <div className="max-h-[60vh] overflow-y-auto divide-y divide-zinc-100">
              {withCoords.map((s) => (
                <button
                  key={s.station_id}
                  onClick={() => setSelected(selected?.station_id === s.station_id ? null : s)}
                  className={`w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-zinc-50 transition-colors ${
                    selected?.station_id === s.station_id ? "bg-blue-50" : ""
                  }`}
                >
                  <MapPin
                    className="h-4 w-4 flex-shrink-0"
                    style={{ color: delayColorHex(s.avg_delay_min) }}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-zinc-800">
                      {s.station_name ?? `Stacja ${s.station_id}`}
                    </p>
                    <p className="text-xs text-zinc-400">
                      {s.avg_delay_min !== null
                        ? `śr. ${s.avg_delay_min.toFixed(1)} min`
                        : "brak danych"}{" "}
                      · {s.total_stops} pomiarów
                    </p>
                  </div>
                  {s.delay_rate_pct !== null && (
                    <span className="text-xs font-medium text-zinc-500">
                      {s.delay_rate_pct.toFixed(0)}%
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* Detail panel */}
          {selected && (
            <div className="w-64 rounded-lg border border-zinc-200 bg-white p-4 shadow-sm space-y-3 self-start">
              <h3 className="font-semibold text-zinc-800">
                {selected.station_name ?? `Stacja ${selected.station_id}`}
              </h3>
              <dl className="space-y-1.5 text-sm">
                <Row label="Śr. opóźnienie" value={`${selected.avg_delay_min?.toFixed(1) ?? "—"} min`} />
                <Row label="Odsetek opóźnień" value={`${selected.delay_rate_pct?.toFixed(1) ?? "—"}%`} />
                <Row label="Pomiarów" value={selected.total_stops} />
                <Row label="Lat" value={selected.latitude?.toFixed(4) ?? "—"} />
                <Row label="Lon" value={selected.longitude?.toFixed(4) ?? "—"} />
              </dl>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-zinc-500">{label}</dt>
      <dd className="font-medium text-zinc-800">{value}</dd>
    </div>
  );
}
