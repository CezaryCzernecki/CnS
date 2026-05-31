"use client";

import { useEffect, useState, useCallback } from "react";
import { format, parseISO } from "date-fns";
import { pl } from "date-fns/locale";
import { RefreshCw, AlertCircle, Train, Clock } from "lucide-react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  type SortingState,
  type ColumnDef,
} from "@tanstack/react-table";
import { fetchActiveDelays, type ActiveDelay } from "@/lib/api";

function delayColor(min: number | null) {
  if (min === null) return "text-zinc-400";
  if (min <= 0) return "text-green-600";
  if (min <= 5) return "text-yellow-600";
  if (min <= 15) return "text-orange-600";
  return "text-red-600";
}

function formatTimestamp(ts: string | null) {
  if (!ts) return "—";
  try {
    return format(parseISO(ts), "HH:mm", { locale: pl });
  } catch {
    return ts.slice(11, 16) || "—";
  }
}

const columns: ColumnDef<ActiveDelay>[] = [
  {
    accessorKey: "station_name",
    header: "Stacja",
    cell: ({ getValue }) => (
      <span className="font-medium text-zinc-800">{getValue<string>() ?? "—"}</span>
    ),
  },
  {
    accessorKey: "delay_departure_min",
    header: "Opóźnienie",
    cell: ({ getValue }) => {
      const v = getValue<number | null>();
      return (
        <span className={`font-bold ${delayColor(v)}`}>
          {v !== null ? `${v > 0 ? "+" : ""}${v} min` : "—"}
        </span>
      );
    },
  },
  {
    accessorKey: "planned_departure",
    header: "Planowy odjazd",
    cell: ({ getValue }) => formatTimestamp(getValue<string | null>()),
  },
  {
    accessorKey: "actual_departure",
    header: "Rzeczywisty",
    cell: ({ getValue }) => formatTimestamp(getValue<string | null>()),
  },
  {
    accessorKey: "schedule_id",
    header: "Rozkład",
    cell: ({ getValue }) => (
      <span className="font-mono text-xs text-zinc-500">{getValue<number>()}</span>
    ),
  },
];

export default function DelaysPage() {
  const [delays, setDelays] = useState<ActiveDelay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [sorting, setSorting] = useState<SortingState>([
    { id: "delay_departure_min", desc: true },
  ]);

  const load = useCallback(async () => {
    try {
      const data = await fetchActiveDelays(100);
      setDelays(data);
      setLastRefresh(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Błąd pobierania danych");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, [load]);

  const table = useReactTable({
    data: delays,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const delayed = delays.filter((d) => (d.delay_departure_min ?? 0) > 0).length;
  const maxDelay = delays.reduce(
    (m, d) => Math.max(m, d.delay_departure_min ?? 0),
    0
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-900">Aktywne opóźnienia</h1>
          <p className="text-sm text-zinc-500">Aktualizacja co 30 s • pociągi status P</p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 rounded-md border border-zinc-200 px-3 py-2 text-sm text-zinc-600 hover:bg-zinc-100"
        >
          <RefreshCw className="h-4 w-4" />
          Odśwież
        </button>
      </div>

      {/* Stat chips */}
      {!loading && !error && (
        <div className="flex gap-3 flex-wrap">
          <Chip icon={<Train className="h-4 w-4" />} label="Pociągów" value={delays.length} />
          <Chip
            icon={<AlertCircle className="h-4 w-4 text-orange-500" />}
            label="Z opóźnieniem"
            value={delayed}
            highlight
          />
          <Chip
            icon={<Clock className="h-4 w-4 text-red-500" />}
            label="Maks. opóźnienie"
            value={`${maxDelay} min`}
          />
          {lastRefresh && (
            <Chip
              icon={<RefreshCw className="h-4 w-4" />}
              label="Ostatnia aktualizacja"
              value={format(lastRefresh, "HH:mm:ss")}
            />
          )}
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="space-y-2 animate-pulse">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-12 rounded-md bg-zinc-200" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-200 bg-zinc-50">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => (
                    <th
                      key={header.id}
                      className="px-4 py-3 text-left font-medium text-zinc-600 cursor-pointer select-none hover:text-zinc-900"
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
            <tbody>
              {table.getRowModel().rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className="py-12 text-center text-zinc-400">
                    Brak aktywnych pociągów z opóźnieniem
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-4 py-3">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Chip({
  icon,
  label,
  value,
  highlight = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  highlight?: boolean;
}) {
  return (
    <div
      className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${
        highlight
          ? "border-orange-200 bg-orange-50 text-orange-800"
          : "border-zinc-200 bg-white text-zinc-700"
      }`}
    >
      {icon}
      <span className="text-zinc-500">{label}:</span>
      <span className="font-semibold">{value}</span>
    </div>
  );
}
