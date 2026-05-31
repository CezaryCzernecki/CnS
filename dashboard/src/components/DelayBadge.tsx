interface DelayBadgeProps {
  delay: number | null;
  cancelled?: boolean;
  size?: "sm" | "md";
}

const TIERS = [
  { threshold: 5,  bg: "bg-green-50",  text: "text-green-700",  border: "border-green-200" },
  { threshold: 15, bg: "bg-yellow-50", text: "text-yellow-700", border: "border-yellow-200" },
  { threshold: Infinity, bg: "bg-red-50", text: "text-red-700", border: "border-red-200" },
];

export function DelayBadge({ delay, cancelled = false, size = "sm" }: DelayBadgeProps) {
  const px = size === "sm" ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm";
  const base = `inline-flex items-center rounded-full border font-medium ${px}`;

  if (cancelled) {
    return <span className={`${base} bg-zinc-100 text-zinc-500 border-zinc-200`}>Odwołany</span>;
  }

  if (delay === null) {
    return <span className={`${base} bg-zinc-50 text-zinc-400 border-zinc-200`}>—</span>;
  }

  if (delay <= 0) {
    return <span className={`${base} bg-green-50 text-green-700 border-green-200`}>Na czas</span>;
  }

  const tier = TIERS.find((t) => delay < t.threshold) ?? TIERS[TIERS.length - 1];
  return (
    <span className={`${base} ${tier.bg} ${tier.text} ${tier.border}`}>
      +{delay} min
    </span>
  );
}
