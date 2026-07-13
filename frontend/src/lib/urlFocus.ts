export type UrlFocus = {
  soulId: string;
  subPath: string;
  sessionKey: string | null;
};

const SOULBOARD_SUB_PATH = "soulboard";

function getRelativePathSegments(): string[] {
  const basePath = new URL(document.baseURI).pathname;
  const normalizedBasePath = basePath.endsWith("/") ? basePath : `${basePath}/`;
  const currentPath = window.location.pathname;
  if (!currentPath.startsWith(normalizedBasePath)) {
    return [];
  }
  return currentPath
    .slice(normalizedBasePath.length)
    .split("/")
    .filter(Boolean)
    .map((segment) => decodeURIComponent(segment));
}

function buildFocusUrl(soulId: string, sessionKey: string | null): URL {
  const url = new URL(window.location.href);
  const baseUrl = new URL(document.baseURI);
  const basePath = baseUrl.pathname.endsWith("/") ? baseUrl.pathname : `${baseUrl.pathname}/`;
  url.pathname = soulId
    ? `${basePath}${encodeURIComponent(soulId)}/${SOULBOARD_SUB_PATH}`
    : basePath;
  if (soulId && sessionKey) {
    url.searchParams.set("session-key", sessionKey);
  } else {
    url.searchParams.delete("session-key");
  }
  return url;
}

export function getFocusFromUrl(): UrlFocus {
  const [soulId = "", ...subPathSegments] = getRelativePathSegments();
  const params = new URLSearchParams(window.location.search);
  return {
    soulId,
    subPath: subPathSegments.join("/"),
    sessionKey: params.get("session-key"),
  };
}

export function syncFocusToUrl(soulId: string, sessionKey: string | null): void {
  window.history.replaceState({}, "", buildFocusUrl(soulId, sessionKey));
}

export function navigateToFocus(soulId: string, sessionKey: string | null = null): void {
  window.history.pushState({}, "", buildFocusUrl(soulId, sessionKey));
}
