"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { format, parseISO } from "date-fns";
import { RefreshCw, Search, Train, AlertCircle, Clock } from "lucide-react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type SortingState,
  type ColumnDef,
  type ColumnFiltersState,
} from "@tanstack/react-table";
import { fetchActiveDelays, type ActiveDelay } from "@/lib/api";
import { DelayBadge } from "@/components/DelayBadge";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { ErrorBanner } from "@/components/ErrorBanner";

// ---------------------------------------------------------------------------
// Stałe
// ---------------------------------------------------------------------------

const REFRESH_INTERVAL_S = 60;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  try {
    return format(parseISO(ts), "HH:mm");
  } catch {
    return ts.slice(11, 16) || "—";
  }
}

function rowBgClass(delay: number | null): string {
  if (delay === null) return "";
  if (delay < 5) return "bg-green-50/70";
  if (delay <= 15) return "bg-yellow-50/70";
  return "bg-red-50/70";
}

// ---------------------------------------------------------------------------
// Definicja kolumn
// ---------------------------------------------------------------------------

const columns: ColumnDef<ActiveDelay>[] = [
  {
    accessorKey: "station_name",
    header: "Stacja",
    cell: ({ getValue }) => (
      <span className="font-medium text-zinc-800">{getValue<string>() ?? "—"}</span>
    ),
    filterFn: "includesString",
  },
  {
    accessorKey: "train_number",
    header: "Pociąg",
    cell: ({ getValue }) => (
      <span className="font-mono text-xs text-zinc-500">
        {getValue<string>() ?? "—"}
      </span>
    ),
    enableColumnFilter: false,
  },
  {
    accessorKey: "delay_departure_min",
    header: "Opóźnienie",
    cell: ({ getValue }) => (
      <DelayBadge delay={getValue<number | null>()} />
    ),
    enableColumnFilter: false,
  },
  {
    accessorKey: "planned_departure",
    header: "Plan. odjazd",
    cell: ({ getValue }) => (
      <span className="text-zinc-600">{formatTime(getValue<string | null>())}</span>
    ),
    enableColumnFilter: false,
  },
  {
    accessorKey: "snapshot_time",
    header: "Aktualizacja",
    cell: ({ getValue }) => (
      <span className="text-xs text-zinc-400">{formatTime(getValue<string | null>())}</span>
    ),
    enableColumnFilter: false,
  },
];

// ---------------------------------------------------------------------------
// Strona
// ---------------------------------------------------------------------------

export default function DelaysPage() {
  const [delays, setDelays] = useState<ActiveDelay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [sorting, setSorting] = useState<SortingState>([
    { id: "delay_departure_min", desc: true },
  ]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  // Ticker co sekundę dla odliczania
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const countdown = lastRefresh
    ? Math.max(0, REFRESH_INTERVAL_S - Math.floor((now - lastRefresh) / 1000))
    : REFRESH_INTERVAL_S;

  const load = useCallback(async () => {
    try {
      const data = await fetchActiveDelays(200);
      setDelays(data);
      setLastRefresh(Date.now());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Błąd pobierania danych");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, REFRESH_INTERVAL_S * 1000);
    return () => clearInterval(interval);
  }, [load]);

  // Statystyki
  const delayed = useMemo(
    () => delays.filter((d) => (d.delay_departure_min ?? 0) > 0).length,
    [delays]
  );
  const maxDelay = useMemo(
    () => delays.reduce((m, d) => Math.max(m, d.delay_departure_min ?? 0), 0),
    [delays]
  );

  // Filtr nazwy stacji
  const stationFilter =
    (columnFilters.find((f) => f.id === "station_name")?.value as string) ?? "";

  const table = useReactTable({
    data: delays,
    columns,
    state: { sorting, columnFilters },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-zinc-900">Aktywne opóźnienia</h1>
          <p className="text-sm text-zinc-500">Pociągi w trasie (status P) · odświeżanie co {REFRESH_INTERVAL_S} s</p>
        </div>
        <div className="flex items-center gap-2">
          {/* Countdown badge */}
          <span
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium ${
              countdown <= 10
                ? "border-orange-200 bg-orange-50 text-orange-700"
                : "border-zinc-200 bg-zinc-50 text-zinc-600"
            }`}
          >
            <Clock className="h-3 w-3" />
            {countdown}s
          </span>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-sm text-zinc-600 hover:bg-zinc-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Odśwież
          </button>
        </div>
      </div>

      {/* Statystyki */}
      {!loading && !error && (
        <div className="flex flex-wrap gap-2">
          <StatChip icon={<Train className="h-3.5 w-3.5" />} label="Pociągów w trasie" value={delays.length} />
          <StatChip
            icon={<AlertCircle className="h-3.5 w-3.5 text-orange-500" />}
            label="Aktualnie opóźnionych"
            value={delayed}
            accent
          />
          <StatChip
            icon={<Clock className="h-3.5 w-3.5 text-red-500" />}
            label="Maks. opóźnienie"
            value={`${maxDelay} min`}
          />
        </div>
      )}

      {/* Licznik filtrowanych */}
      {!loading && !error && (
        <p className="text-sm font-medium text-zinc-600">
          Aktualnie opóźnionych:{" "}
          <span className="text-zinc-900 font-bold">{delayed}</span>
          {stationFilter && (
            <span className="text-zinc-400 font-normal">
              {" "}· wyświetlono {table.getFilteredRowModel().rows.length} po filtrze
            </span>
          )}
        </p>
      )}

      {/* Filtr stacji */}
      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
        <input
          type="text"
          placeholder="Filtruj po nazwie stacji..."
          value={stationFilter}
          onChange={(e) =>
            table.getColumn("station_name")?.setFilterValue(e.target.value)
          }
          className="w-full rounded-md border border-zinc-300 bg-white py-2 pl-9 pr-3 text-sm placeholder:text-zinc-400 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
        {stationFilter && (
          <button
            onClick={() => table.getColumn("station_name")?.setFilterValue("")}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
          >
            ×
          </button>
        )}
      </div>

      {/* Treść */}
      {loading ? (
        <div className="flex h-48 items-center justify-center">
          <LoadingSpinner />
        </div>
      ) : error ? (
        <ErrorBanner message={error} onRetry={load} />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-200 bg-zinc-50">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => (
                    <th
                      key={header.id}
                      className="cursor-pointer select-none px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 hover:text-zinc-800"
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {header.column.getIsSorted() === "asc"
                        ? " ↑"
                        : header.column.getIsSorted() === "desc"
                          ? " ↓"
                          : ""}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody className="divide-y divide-zinc-100">
              {table.getRowModel().rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="py-16 text-center text-sm text-zinc-400"
                  >
                    {stationFilter
                      ? `Brak pociągów dla stacji „${stationFilter}"`
                      : "Brak aktywnych pociągów z opóźnieniem"}
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => {
                  const delay = row.original.delay_departure_min;
                  return (
                    <tr
                      key={row.id}
                      className={`transition-colors hover:brightness-95 ${rowBgClass(delay)}`}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-4 py-3">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
          <div className="border-t border-zinc-100 px-4 py-2 text-xs text-zinc-400">
            {table.getFilteredRowModel().rows.length} / {delays.length} wierszy
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Podkomponenty
// ---------------------------------------------------------------------------

function StatChip({
  icon,
  label,
  value,
  accent = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  accent?: boolean;
}) {
  return (
    <div
      className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm ${
        accent
          ? "border-orange-200 bg-orange-50 text-orange-800"
          : "border-zinc-200 bg-white text-zinc-600"
      }`}
    >
      {icon}
      <span className="text-zinc-500 text-xs">{label}:</span>
      <span className="font-semibold">{value}</span>
    </div>
  );
}
