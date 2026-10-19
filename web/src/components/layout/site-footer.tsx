import { Link } from "react-router";
import { Logo } from "@/components/ui/logo";
import { DOCS_URL, REPO_URL } from "@/lib/utils";

export function SiteFooter() {
  return (
    <footer className="border-t border-line bg-bg">
      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-12 sm:px-6 md:grid-cols-[1.4fr_1fr_1fr] lg:px-8">
        <div className="space-y-3">
          <Logo />
          <p className="max-w-sm text-[14px] leading-relaxed text-muted">
            License-clean distillation on open models. Built by Harsh Bhanushali (DualMind Labs) for the Nebius x NVIDIA Global AI Hackathon.
          </p>
          <p className="text-[13px] text-muted">Code under Apache-2.0. Model weights keep their own licenses.</p>
        </div>
        <div>
          <h2 className="mb-3 text-[13px] font-semibold text-ink">Product</h2>
          <ul className="space-y-2 text-[14px] text-muted">
            <li><Link className="hover:text-ink" to="/">The story</Link></li>
            <li><Link className="hover:text-ink" to="/scout">Scout demo</Link></li>
            <li><Link className="hover:text-ink" to="/dashboard">Dashboard</Link></li>
          </ul>
        </div>
        <div>
          <h2 className="mb-3 text-[13px] font-semibold text-ink">Open source</h2>
          <ul className="space-y-2 text-[14px] text-muted">
            <li><a className="hover:text-ink" href={REPO_URL} target="_blank" rel="noopener noreferrer">GitHub repository</a></li>
            <li><a className="hover:text-ink" href={DOCS_URL} target="_blank" rel="noopener noreferrer">Documentation</a></li>
            <li><a className="hover:text-ink" href={`${REPO_URL}/blob/main/docs/PRD.md`} target="_blank" rel="noopener noreferrer">Product spec</a></li>
          </ul>
        </div>
      </div>
    </footer>
  );
}
