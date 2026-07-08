import { toast } from "sonner";

export function getErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export function notifyError(cause: unknown): void {
  toast.error(getErrorMessage(cause), { id: "global-error" });
}
