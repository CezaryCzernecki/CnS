"use client";

import { useEffect, useRef, useState } from "react";
import { fetchMapStations, type StationMapPoint } from "@/lib/api";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { ErrorBanner } from "@/components/ErrorBanner";

// ---------------------------------------------------------------------------
// Typy
// ---------------------------------------------------------------------------

interface Tooltip {
  x: number;
  y: number;
  name: string | null;
  delay: number | null;
  rate: number | null;
  stops: number;
}

// ---------------------------------------------------------------------------
// Progi kolorów (minuty)
// ---------------------------------------------------------------------------

const COLOR_SCALE = [
  { min: 0,  max: 3,        color: "#22c55e", label: "0–3 min" },
  { min: 3,  max: 8,        color: "#eab308", label: "3–8 min" },
  { min: 8,  max: 15,       color: "#f97316", label: "8–15 min" },
  { min: 15, max: Infinity,  color: "#ef4444", label: ">15 min" },
];
const NO_DATA_COLOR = "#94a3b8";

// Maplibre expression dla interpolacji koloru wg avg_delay_min
const CIRCLE_COLOR_EXPR = [
  "interpolate", ["linear"],
  ["coalesce", ["get", "avg_delay_min"], 0],
  0,  "#22c55e",
  3,  "#22c55e",
  3,  "#eab308",
  8,  "#eab308",
  8,  "#f97316",
  15, "#f97316",
  15, "#ef4444",
  40, "#ef4444",
] as unknown[];

const CIRCLE_RADIUS_EXPR = [
  "interpolate", ["linear"],
  ["coalesce", ["get", "avg_delay_min"], 0],
  0,  5,
  3,  7,
  8,  11,
  15, 16,
  30, 20,
] as unknown[];

// ---------------------------------------------------------------------------
// Komponent
// ---------------------------------------------------------------------------

export default function MapPage() {
  const mapContainer = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);

  const [stations, setStations] = useState<StationMapPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  // Pobierz dane stacji
  useEffect(() => {
    fetchMapStations(80)
      .then(setStations)
      .catch((e) => setError(e instanceof Error ? e.message : "Błąd pobierania"))
      .finally(() => setLoading(false));
  }, []);

  // Inicjalizacja mapy
  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    import("maplibre-gl").then((mgl: any) => {
      if (!mapContainer.current) return;

      const map = new mgl.Map({
        container: mapContainer.current,
        style: "https://demotiles.maplibre.org/style.json",
        center: [19.1, 52.0],
        zoom: 6,
        attributionControl: { compact: true },
      });

      map.addControl(new mgl.NavigationControl({ showCompass: false }), "top-right");
      mapRef.current = map;
    });

    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []);

  // Dodaj dane stacji do mapy gdy są gotowe
  useEffect(() => {
    if (!mapRef.current || !stations.length) return;

    const map = mapRef.current;
    const features = stations
      .filter((s) => s.latitude !== null && s.longitude !== null)
      .map((s) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [s.longitude!, s.latitude!] },
        properties: {
          station_name: s.station_name,
          avg_delay_min: s.avg_delay_min,
          delay_rate_pct: s.delay_rate_pct,
          total_stops: s.total_stops,
        },
      }));

    const geojson = { type: "FeatureCollection" as const, features };

    const addData = () => {
      const existing = map.getSource("stations");
      if (existing) {
        existing.setData(geojson);
        return;
      }

      map.addSource("stations", { type: "geojson", data: geojson });

      map.addLayer({
        id: "stations-circle",
        type: "circle",
        source: "stations",
        paint: {
          "circle-radius": CIRCLE_RADIUS_EXPR,
          "circle-color": CIRCLE_COLOR_EXPR,
          "circle-opacity": 0.88,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
        },
      });

      // Kursor pointer na hover
      map.on("mouseenter", "stations-circle", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "stations-circle", () => {
        map.getCanvas().style.cursor = "";
        setTooltip(null);
      });

      // Tooltip na hover
      map.on("mousemove", "stations-circle", (e: {
        point: { x: number; y: number };
        features?: { properties: Record<string, unknown> }[];
      }) => {
        const f = e.features?.[0];
        if (!f) { setTooltip(null); return; }
        const p = f.properties;
        setTooltip({
          x: e.point.x,
          y: e.point.y,
          name: p.station_name as string | null,
          delay: p.avg_delay_min as number | null,
          rate: p.delay_rate_pct as number | null,
          stops: p.total_stops as number,
        });
      });
    };

    if (map.isStyleLoaded()) {
      addData();
    } else {
      map.once("load", addData);
    }
  }, [stations]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-zinc-900">Mapa opóźnień PKP</h1>
          <p className="text-sm text-zinc-500">
            Średnie opóźnienia wg stacji · ostatnie 7 dni · {stations.length} stacji
          </p>
        </div>
        {!loading && !error && (
          <p className="text-sm text-zinc-400">
            {stations.filter((s) => s.latitude).length} stacji z koordynatami
          </p>
        )}
      </div>

      {error && <ErrorBanner message={error} />}

      {/* Legenda */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-600">
        <span className="font-medium">Śr. opóźnienie:</span>
        {COLOR_SCALE.map(({ color, label }) => (
          <span key={label} className="flex items-center gap-1">
            <span
              className="inline-block h-3 w-3 rounded-full border border-white shadow-sm"
              style={{ backgroundColor: color }}
            />
            {label}
          </span>
        ))}
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-3 w-3 rounded-full border border-white shadow-sm"
            style={{ backgroundColor: NO_DATA_COLOR }}
          />
          brak danych
        </span>
        <span className="text-zinc-400">· rozmiar = wielkość opóźnienia</span>
      </div>

      {/* Kontener mapy */}
      <div className="relative overflow-hidden rounded-xl border border-zinc-200 shadow-sm">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-zinc-50">
            <div className="flex flex-col items-center gap-3">
              <LoadingSpinner size="lg" />
              <p className="text-sm text-zinc-500">Pobieranie danych stacji…</p>
            </div>
          </div>
        )}

        <div ref={mapContainer} style={{ height: 580 }} />

        {/* Tooltip */}
        {tooltip && (
          <div
            className="pointer-events-none absolute z-20 w-52 rounded-lg border border-zinc-200 bg-white p-3 shadow-lg"
            style={{
              left: Math.min(tooltip.x + 12, window.innerWidth - 230),
              top: tooltip.y - 10,
              transform: "translateY(-100%)",
            }}
          >
            <p className="mb-1.5 font-semibold text-zinc-800 text-sm leading-tight">
              {tooltip.name ?? "Nieznana stacja"}
            </p>
            <dl className="space-y-0.5 text-xs">
              <TooltipRow label="Śr. opóźnienie" value={tooltip.delay !== null ? `${tooltip.delay.toFixed(1)} min` : "—"} />
              <TooltipRow label="Opóźnionych" value={tooltip.rate !== null ? `${tooltip.rate.toFixed(0)}%` : "—"} />
              <TooltipRow label="Pomiarów (7 dni)" value={tooltip.stops.toLocaleString("pl-PL")} />
            </dl>
          </div>
        )}
      </div>
    </div>
  );
}

function TooltipRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-zinc-500">{label}:</dt>
      <dd className="font-medium text-zinc-800">{value}</dd>
    </div>
  );
}
