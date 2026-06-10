import { Activity } from "lucide-react";

import { type EndpointStatus } from "./shared";

export const StatusBadge = ({
  status,
}: {
  status: EndpointStatus | "online" | "offline" | "draining";
}) => {
  const colors = {
    online: "bg-green-500/20 text-green-400 border-green-500/30",
    degraded: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
    draining: "bg-amber-500/20 text-amber-300 border-amber-500/30",
    offline: "bg-red-500/20 text-red-400 border-red-500/30",
  };

  const labels: Record<string, string> = {
    online: "Online",
    degraded: "Degraded",
    draining: "Draining",
    offline: "Offline",
  };
  const dotColor =
    status === "online"
      ? "bg-green-400"
      : status === "offline"
        ? "bg-red-400"
        : "bg-yellow-400";

  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-medium border ${
        colors[status]
      } flex items-center gap-1.5`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${dotColor} animate-pulse`}
      />
      {labels[status]}
    </span>
  );
};

export const LatencyBar = ({ ms }: { ms: number }) => {
  let color = "bg-green-500";
  if (ms > 300) color = "bg-yellow-500";
  if (ms > 800) color = "bg-red-500";
  if (ms === 0) color = "bg-gray-700";

  const width = Math.min((ms / 1000) * 100, 100);

  return (
    <div className="flex items-center gap-2 text-xs text-gray-400 mt-2">
      <Activity size={12} />
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div
          className={`h-full ${color} transition-all duration-500`}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className="w-12 text-right font-mono">
        {ms > 0 ? `${ms}ms` : "N/A"}
      </span>
    </div>
  );
};
