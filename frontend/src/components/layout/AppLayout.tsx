import { Link, useLocation } from "react-router-dom";

import { cn } from "@/lib/utils";

const navItems = [
  { label: "大盘看板", path: "/" },
  { label: "API 资产管理", path: "/assets" },
  { label: "路由测试台", path: "/router-lab" },
];

export const AppLayout = ({ children }: { children: React.ReactNode }) => {
  const location = useLocation();

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-muted bg-panel/80">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-zinc-400">
              LLM API Factory
            </p>
            <h1 className="text-lg font-semibold">代理监控控制台</h1>
          </div>
          <nav className="flex gap-3 text-sm">
            {navItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                className={cn(
                  "rounded-full px-3 py-1 transition",
                  location.pathname === item.path
                    ? "bg-white text-black"
                    : "text-zinc-300 hover:text-white"
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>
    </div>
  );
};
