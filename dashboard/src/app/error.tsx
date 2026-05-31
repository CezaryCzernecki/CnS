"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

export default function Error({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center gap-4 py-20 text-center">
      <AlertTriangle className="h-12 w-12 text-red-400" />
      <h2 className="text-xl font-semibold text-zinc-800">Coś poszło nie tak</h2>
      <p className="max-w-sm text-sm text-zinc-500">
        {error.message || "Nie udało się załadować danych. Sprawdź czy API jest dostępne."}
      </p>
      <button
        onClick={unstable_retry}
        className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
      >
        Spróbuj ponownie
      </button>
    </div>
  );
}
