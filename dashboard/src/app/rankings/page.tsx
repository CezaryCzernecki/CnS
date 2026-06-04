"use client";

import { useEffect, useState, useCallback } from "react";
import { Trophy, Calendar, Train, Building2, RefreshCw } from "lucide-react";
import { DelayBadge } from "@/components/DelayBadge";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { ErrorBanner } from "@/components/ErrorBanner";
import {
  fetchRankingsAllTime,
  fetchRankingsDaily,
  fetchRankingsMonthlyTrains,
  fetchRankingsMonthlyCarriers,
  type AllTimeRankingEntry,
  type DailyRankingEntry,
  type MonthlyTrainRankingEntry,
  type MonthlyCarrierRankingEntry,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function cleanCarrier(name: string | null): string {
  if (!name) return "—";
  return name
    .replace(/\s+S\.A\./gi, "")
    .replace(/\s+Sp\.\s*z\s*o\.o\./gi, "")
    .replace(/\s+spółka\s+akcyjna/gi, "")
    .replace(/\s+spółka\s+z\s+o\.o\./gi, "")
    .trim();
}

function fmtDate(dateStr: string | null): string {
  if (!dateStr) return "—";
  const m = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : dateStr;
}

// ---------------------------------------------------------------------------
// Wspólne komponenty
// ---------------------------------------------------------------------------

const LIMITS = [10, 25, 50, 100] as const;
type Limit = (typeof LIMITS)[number];

function LimitSelector({
  value,
  onChange,
}: {
  value: Limit;
  onChange: (v: Limit) => void;
}) {
  return (
    <div className="flex items-center gap-1 rounded-md border border-zinc-200 bg-zinc-50 p-0.5">
      {LIMITS.map((l) => (
        <button
          key={l}
          onClick={() => onChange(l)}
          className={[
            "rounded px-2.5 py-1 text-xs font-semibold transition-colors",
            value === l
              ? "bg-white shadow-sm text-blue-700"
              : "text-zinc-500 hover:text-zinc-800",
          ].join(" ")}
        >
          Top {l}
        </button>
      ))}
    </div>
  );
}

function RankTable({
  headers,
  rows,
  loading,
  error,
  onRetry,
}: {
  headers: string[];
  rows: React.ReactNode[][];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (loading)
    return (
      <div className="flex h-48 items-center justify-center">
        <LoadingSpinner />
      </div>
    );
  if (error) return <ErrorBanner message={error} onRetry={onRetry} />;

  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white shadow-sm">
      <table className="w-full text-sm">
        <thead className="border-b border-zinc-200 bg-zinc-50">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 w-10">
              #
            </th>
            {headers.map((h) => (
              <th
                key={h}
                className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-zinc-500"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={headers.length + 1}
                className="py-16 text-center text-sm text-zinc-400"
              >
                Brak danych
              </td>
            </tr>
          ) : (
            rows.map((cells, i) => (
              <tr key={i} className="hover:bg-zinc-50/60 transition-colors">
                <td className="px-4 py-3 text-xs font-bold text-zinc-400 tabular-nums">
                  {i + 1}
                </td>
                {cells.map((cell, j) => (
                  <td key={j} className="px-4 py-3">
                    {cell}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
      <div className="border-t border-zinc-100 px-4 py-2 text-xs text-zinc-400">
        {rows.length} wyników
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Wszech czasów
// ---------------------------------------------------------------------------

function AllTimeTab() {
  const [limit, setLimit] = useState<Limit>(10);
  const [data, setData] = useState<AllTimeRankingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchRankingsAllTime(limit));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Błąd pobierania danych");
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = data.map((r) => [
    <TrainCell key="t" number={r.train_number} name={r.train_name} carrier={r.carrier_name} />,
    <span key="c" className="text-zinc-600 text-sm">{cleanCarrier(r.carrier_name)}</span>,
    <RouteCell key="r" first={r.first_station} last={r.last_station} />,
    <span key="d" className="text-zinc-500 text-xs tabular-nums">{fmtDate(r.operating_date)}</span>,
    <DelayBadge key="b" delay={r.max_delay_min} />,
    <BusReplacementCell key="kz" active={r.has_bus_replacement} segment={r.bus_segment} />,
  ]);

  return (
    <TabLayout
      title="Rekordy opóźnień"
      subtitle="Najwyższe opóźnienia od początku notowań — jeden rekord na kurs pociągu"
      limit={limit}
      onLimitChange={setLimit}
      onRefresh={load}
      loading={loading}
    >
      <RankTable
        headers={["Pociąg", "Przewoźnik", "Trasa", "Data kursu", "Maks. opóźnienie", "Kom. zastępcza"]}
        rows={rows}
        loading={loading}
        error={error}
        onRetry={load}
      />
    </TabLayout>
  );
}

// ---------------------------------------------------------------------------
// Tab: Ranking dzienny
// ---------------------------------------------------------------------------

function DailyTab() {
  const [limit, setLimit] = useState<Limit>(10);
  const [date, setDate] = useState<string>(todayIso());
  const [data, setData] = useState<DailyRankingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!date) return;
    setLoading(true);
    setError(null);
    try {
      setData(await fetchRankingsDaily(date, limit));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Błąd pobierania danych");
    } finally {
      setLoading(false);
    }
  }, [date, limit]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = data.map((r) => [
    <TrainCell key="t" number={r.train_number} name={r.train_name} carrier={r.carrier_name} />,
    <span key="c" className="text-zinc-600 text-sm">{cleanCarrier(r.carrier_name)}</span>,
    <RouteCell key="r" first={r.first_station} last={r.last_station} />,
    <DelayBadge key="b" delay={r.max_delay_min} />,
  ]);

  return (
    <TabLayout
      title="Ranking dzienny"
      subtitle="Najwyższe opóźnienia w wybranym dniu — jeden rekord na kurs pociągu"
      limit={limit}
      onLimitChange={setLimit}
      onRefresh={load}
      loading={loading}
      extra={
        <input
          type="date"
          value={date}
          max={todayIso()}
          onChange={(e) => setDate(e.target.value)}
          className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm text-zinc-700 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
      }
    >
      <RankTable
        headers={["Pociąg", "Przewoźnik", "Trasa", "Maks. opóźnienie"]}
        rows={rows}
        loading={loading}
        error={error}
        onRetry={load}
      />
    </TabLayout>
  );
}

// ---------------------------------------------------------------------------
// Tab: Miesięczny – pociągi
// ---------------------------------------------------------------------------

function MonthlyTrainsTab() {
  const now = new Date();
  const [limit, setLimit] = useState<Limit>(10);
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [data, setData] = useState<MonthlyTrainRankingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchRankingsMonthlyTrains(year, month, limit));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Błąd pobierania danych");
    } finally {
      setLoading(false);
    }
  }, [year, month, limit]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = data.map((r) => [
    <TrainCell key="t" number={r.train_number} name={r.train_name} carrier={r.carrier_name} />,
    <span key="c" className="text-zinc-600 text-sm">{cleanCarrier(r.carrier_name)}</span>,
    <span key="tc" className="text-zinc-500 text-xs tabular-nums">{r.trip_count}</span>,
    <span key="td" className="font-semibold text-red-700 tabular-nums text-sm">
      {r.total_delay_min ?? "—"} min
    </span>,
    <span key="ad" className="text-zinc-500 text-xs tabular-nums">
      {r.avg_delay_min != null ? `${r.avg_delay_min} min` : "—"}
    </span>,
  ]);

  return (
    <TabLayout
      title="Sumaryczne opóźnienia — pociągi"
      subtitle="Suma maksymalnych opóźnień kursów w wybranym miesiącu, posortowana po numerze pociągu"
      limit={limit}
      onLimitChange={setLimit}
      onRefresh={load}
      loading={loading}
      extra={<MonthPicker year={year} month={month} onChange={(y, m) => { setYear(y); setMonth(m); }} />}
    >
      <RankTable
        headers={["Pociąg", "Przewoźnik", "Kursów", "Łączne opóźnienie", "Śr. opóźnienie"]}
        rows={rows}
        loading={loading}
        error={error}
        onRetry={load}
      />
    </TabLayout>
  );
}

// ---------------------------------------------------------------------------
// Tab: Miesięczny – spółki
// ---------------------------------------------------------------------------

function MonthlyCarriersTab() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [data, setData] = useState<MonthlyCarrierRankingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchRankingsMonthlyCarriers(year, month));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Błąd pobierania danych");
    } finally {
      setLoading(false);
    }
  }, [year, month]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = data.map((r) => [
    <span key="c" className="font-semibold text-zinc-800 text-sm">{cleanCarrier(r.carrier_name)}</span>,
    <span key="tc" className="text-zinc-500 text-xs tabular-nums">{r.trip_count}</span>,
    <span key="td" className="font-semibold text-red-700 tabular-nums text-sm">
      {r.total_delay_min ?? "—"} min
    </span>,
    <span key="ad" className="text-zinc-500 text-xs tabular-nums">
      {r.avg_delay_min != null ? `${r.avg_delay_min} min` : "—"}
    </span>,
  ]);

  return (
    <TabLayout
      title="Sumaryczne opóźnienia — spółki"
      subtitle="Suma maksymalnych opóźnień kursów w wybranym miesiącu — wszyscy przewoźnicy"
      onRefresh={load}
      loading={loading}
      extra={<MonthPicker year={year} month={month} onChange={(y, m) => { setYear(y); setMonth(m); }} />}
    >
      <RankTable
        headers={["Przewoźnik", "Kursów", "Łączne opóźnienie", "Śr. opóźnienie"]}
        rows={rows}
        loading={loading}
        error={error}
        onRetry={load}
      />
    </TabLayout>
  );
}

// ---------------------------------------------------------------------------
// Podkomponenty
// ---------------------------------------------------------------------------

function TrainCell({
  number,
  name,
  carrier,
}: {
  number: string | null;
  name: string | null;
  carrier: string | null;
}) {
  const secondary = [cleanCarrier(carrier), name].filter((s) => s && s !== "—").join(" · ");
  return (
    <div className="flex flex-col gap-0.5 min-w-[90px]">
      <span className="font-semibold text-zinc-900 font-mono text-sm">
        {number ?? "—"}
      </span>
      {secondary && <span className="text-xs text-zinc-400">{secondary}</span>}
    </div>
  );
}

function RouteCell({
  first,
  last,
}: {
  first: string | null;
  last: string | null;
}) {
  if (!first && !last) return <span className="text-zinc-400 text-xs">—</span>;
  return (
    <div className="flex flex-col gap-0.5 min-w-[140px]">
      <span className="text-zinc-700 text-xs">{first ?? "—"}</span>
      <span className="text-zinc-400 text-xs">↓ {last ?? "—"}</span>
    </div>
  );
}

function BusReplacementCell({
  active,
  segment,
}: {
  active: boolean;
  segment: string | null;
}) {
  if (!active) return <span className="text-zinc-300 text-xs">—</span>;
  return (
    <div className="flex flex-col gap-0.5">
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">
        🚌 KZ
      </span>
      {segment && (
        <span className="text-xs text-zinc-500">{segment}</span>
      )}
    </div>
  );
}

function MonthPicker({
  year,
  month,
  onChange,
}: {
  year: number;
  month: number;
  onChange: (y: number, m: number) => void;
}) {
  const value = `${year}-${String(month).padStart(2, "0")}`;
  const now = new Date();
  const max = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

  return (
    <input
      type="month"
      value={value}
      max={max}
      min="2025-01"
      onChange={(e) => {
        const [y, m] = e.target.value.split("-").map(Number);
        if (y && m) onChange(y, m);
      }}
      className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm text-zinc-700 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
    />
  );
}

function TabLayout({
  title,
  subtitle,
  limit,
  onLimitChange,
  onRefresh,
  loading,
  extra,
  children,
}: {
  title: string;
  subtitle: string;
  limit?: Limit;
  onLimitChange?: (v: Limit) => void;
  onRefresh: () => void;
  loading: boolean;
  extra?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-zinc-900">{title}</h2>
          <p className="text-sm text-zinc-500">{subtitle}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {extra}
          {limit !== undefined && onLimitChange && (
            <LimitSelector value={limit} onChange={onLimitChange} />
          )}
          <button
            onClick={onRefresh}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-sm text-zinc-600 hover:bg-zinc-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Odśwież
          </button>
        </div>
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Główna strona
// ---------------------------------------------------------------------------

const TABS = [
  { id: "all-time", label: "Wszech czasów", icon: Trophy },
  { id: "daily", label: "Dzienny", icon: Calendar },
  { id: "monthly-trains", label: "Miesięczny – pociągi", icon: Train },
  { id: "monthly-carriers", label: "Miesięczny – spółki", icon: Building2 },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function RankingsPage() {
  const [activeTab, setActiveTab] = useState<TabId>("all-time");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">Rankingi opóźnień</h1>
        <p className="text-sm text-zinc-500">
          Statystyki historyczne na podstawie zebranych snapshotów
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex flex-wrap gap-1 border-b border-zinc-200 pb-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={[
              "flex items-center gap-1.5 rounded-t-md px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px",
              activeTab === id
                ? "border-blue-600 text-blue-700 bg-blue-50/50"
                : "border-transparent text-zinc-500 hover:text-zinc-800 hover:border-zinc-300",
            ].join(" ")}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div>
        {activeTab === "all-time" && <AllTimeTab />}
        {activeTab === "daily" && <DailyTab />}
        {activeTab === "monthly-trains" && <MonthlyTrainsTab />}
        {activeTab === "monthly-carriers" && <MonthlyCarriersTab />}
      </div>
    </div>
  );
}
