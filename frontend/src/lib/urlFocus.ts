export type UrlFocus = {
  soulId: string;
  sessionKey: string | null;
};

export function getFocusFromUrl(): UrlFocus {
  const params = new URLSearchParams(window.location.search);
  return {
    soulId: params.get("soul-id") ?? "",
    sessionKey: params.get("session-key"),
  };
}

export function syncFocusToUrl(soulId: string, sessionKey: string | null): void {
  const url = new URL(window.location.href);
  if (soulId) {
    url.searchParams.set("soul-id", soulId);
  } else {
    url.searchParams.delete("soul-id");
  }
  if (soulId && sessionKey) {
    url.searchParams.set("session-key", sessionKey);
  } else {
    url.searchParams.delete("session-key");
  }
  window.history.replaceState({}, "", url);
}
