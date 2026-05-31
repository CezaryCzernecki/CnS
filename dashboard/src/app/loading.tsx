export default function Loading() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-8 w-48 rounded-md bg-zinc-200" />
      <div className="space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="h-12 w-full rounded-md bg-zinc-200" />
        ))}
      </div>
    </div>
  );
}
