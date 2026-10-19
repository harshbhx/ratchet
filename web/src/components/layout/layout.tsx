import { useEffect } from "react";
import { Outlet, useLocation } from "react-router";
import { SiteHeader } from "./site-header";
import { SiteFooter } from "./site-footer";

const TITLES: Record<string, string> = {
  "/": "Ratchet: own the model your traffic trains",
  "/scout": "Scout · Ratchet",
  "/dashboard": "Dashboard · Ratchet",
};

export function Layout() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    document.title = TITLES[pathname] ?? "Ratchet";
    if (!hash) window.scrollTo({ top: 0 });
  }, [pathname, hash]);
  return (
    <div className="flex min-h-dvh flex-col bg-bg text-ink">
      <SiteHeader />
      <main id="main" className="flex-1">
        <Outlet />
      </main>
      <SiteFooter />
    </div>
  );
}
