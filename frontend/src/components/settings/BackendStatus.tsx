"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle, ServerCog } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { getSettingsStatus } from "@/lib/api/settings";
import { cn } from "@/lib/utils";

export function BackendStatus() {
  const statusQuery = useQuery({
    queryKey: ["settings-status"],
    queryFn: getSettingsStatus,
  });

  const status = statusQuery.data;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h4 className="flex items-center gap-1.5 text-sm font-medium">
          <ServerCog className="size-4" /> Backend
        </h4>
        {status && (
          <Badge
            variant={status.has_key ? "secondary" : "outline"}
            className={cn(
              "gap-1.5",
              status.has_key && "text-emerald-700 dark:text-emerald-400"
            )}
          >
            {status.has_key ? <CheckCircle2 className="size-3" /> : <XCircle className="size-3" />}
            {status.has_key ? `Connected — ${status.model}` : "No key configured"}
          </Badge>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        Document processing, retrieval, and chat generation run on the server&apos;s own
        configured key — nothing to enter here. Falls back to a local model automatically
        if the configured provider stops working.
      </p>
    </div>
  );
}
