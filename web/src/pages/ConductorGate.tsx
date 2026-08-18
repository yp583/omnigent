import { useQuery } from "@tanstack/react-query";
import { BotIcon, Loader2Icon, RotateCcwIcon } from "lucide-react";
import { Navigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ensureConductor } from "@/lib/conductorApi";

/**
 * Resolve the caller's singleton Conductor without exposing setup mechanics.
 * The server creates it on first use and returns the same transcript on every
 * later open, so this route is only a quiet loading/error seam before chat.
 */
export function ConductorGate() {
  const conductor = useQuery({
    queryKey: ["conductor", "binding"],
    queryFn: ensureConductor,
    retry: false,
  });

  if (conductor.data) {
    return <Navigate to={`/conductor/${conductor.data.conversationId}`} replace />;
  }

  if (conductor.isError) {
    return (
      <main className="flex min-h-full items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <BotIcon className="mx-auto size-5 text-muted-foreground" aria-hidden />
          <h1 className="mt-4 text-base font-semibold">Conductor couldn’t start</h1>
          <p role="alert" className="mt-2 text-sm leading-6 text-muted-foreground">
            {conductor.error instanceof Error
              ? conductor.error.message
              : "The server could not prepare your Conductor chat."}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-5"
            onClick={() => void conductor.refetch()}
          >
            <RotateCcwIcon className="size-3.5" />
            Retry
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-full items-center justify-center text-sm text-muted-foreground">
      <Loader2Icon className="mr-2 size-4 animate-spin motion-reduce:animate-none" aria-hidden />
      Opening Conductor…
    </main>
  );
}
