"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Train, Map, Sparkles } from "lucide-react";

const links = [
  { href: "/delays", label: "Opóźnienia", icon: Train },
  { href: "/map", label: "Mapa", icon: Map },
  { href: "/predict", label: "Predykcja", icon: Sparkles },
];

export default function NavBar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 border-b border-zinc-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
        <Link href="/delays" className="flex items-center gap-2 font-semibold text-zinc-900">
          <Train className="h-5 w-5 text-blue-600" />
          <span>cyrk_na_szynach</span>
        </Link>

        <nav className="flex items-center gap-1">
          {links.map(({ href, label, icon: Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={[
                  "flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-blue-50 text-blue-700"
                    : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900",
                ].join(" ")}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
