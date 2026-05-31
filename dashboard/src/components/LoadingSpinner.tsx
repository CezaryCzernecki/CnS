const SIZE = { sm: "h-4 w-4 border-2", md: "h-8 w-8 border-2", lg: "h-12 w-12 border-4" };

export function LoadingSpinner({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  return (
    <div
      className={`animate-spin rounded-full border-zinc-200 border-t-blue-600 ${SIZE[size]}`}
      role="status"
      aria-label="Ładowanie..."
    />
  );
}
