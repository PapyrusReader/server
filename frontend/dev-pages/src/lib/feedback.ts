export type OutcomeKind = "idle" | "success" | "error" | "warning" | "pending";

export interface OutcomeState {
  kind: OutcomeKind;
  message: string;
  at?: string;
}

export function renderJson(target: HTMLElement, value: unknown): void {
  target.textContent = JSON.stringify(value, null, 2);
}

export function formatTimestamp(timestamp: string | undefined): string {
  if (!timestamp) {
    return "Waiting for activity";
  }

  const date = new Date(timestamp);

  if (Number.isNaN(date.getTime())) {
    return "Waiting for activity";
  }

  const deltaMs = Date.now() - date.getTime();

  if (Math.abs(deltaMs) < 10_000) {
    return "Updated just now";
  }

  return `Updated ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
}

export function extractErrorMessage(body: unknown, fallback: string): string {
  if (typeof body === "string" && body.trim()) {
    return body;
  }

  if (typeof body !== "object" || body === null) {
    return fallback;
  }

  if ("error" in body) {
    const error = body.error;

    if (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string") {
      return error.message;
    }
  }

  if ("detail" in body && typeof body.detail === "string") {
    return body.detail;
  }

  return fallback;
}

export function setButtonBusy(
  button: HTMLButtonElement,
  isBusy: boolean,
  busyLabel: string,
): void {
  if (!button.dataset.defaultLabel) {
    button.dataset.defaultLabel = button.textContent?.trim() ?? "";
  }

  button.dataset.busy = String(isBusy);
  button.disabled = isBusy;
  button.textContent = isBusy ? busyLabel : button.dataset.defaultLabel;
}
