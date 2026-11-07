/** All business decisions live in the API. This is a thin typed client with CSRF handling. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
  }
}

function readCsrfCookie(): string | undefined {
  return document.cookie
    .split("; ")
    .find((row) => row.startsWith("XSRF-TOKEN="))
    ?.slice("XSRF-TOKEN=".length);
}

async function ensureCsrfToken(): Promise<string | undefined> {
  const existing = readCsrfCookie();
  if (existing) return decodeURIComponent(existing);
  await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
  const fetched = readCsrfCookie();
  return fetched ? decodeURIComponent(fetched) : undefined;
}

export type RequestOptions = {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
};

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (method !== "GET") {
    const token = await ensureCsrfToken();
    if (token) headers["X-XSRF-TOKEN"] = token;
  }

  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    signal: options.signal,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as {
      code?: string;
      detail?: string;
      requestId?: string;
    };
    throw new ApiError(
      response.status,
      problem.code ?? `HTTP_${response.status}`,
      problem.detail ?? "That didn't work. Try again.",
      problem.requestId,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const SIGN_IN_URL = "/oauth2/authorization/keycloak";
