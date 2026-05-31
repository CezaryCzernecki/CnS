import { AlertTriangle, RefreshCw } from "lucide-react";

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  return (
    <div className="flex items-center gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      <AlertTriangle className="h-4 w-4 flex-shrink-0" />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="flex items-center gap-1 rounded border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
        >
          <RefreshCw className="h-3 w-3" />
          Ponów
        </button>
      )}
    </div>
  );
}
