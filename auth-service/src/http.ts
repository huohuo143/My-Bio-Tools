import { sha256 } from "./crypto.ts";
import type { Env } from "./types.ts";

export function json(data: unknown, status = 200, requestId?: string): Response {
  return Response.json(
    { requestId: requestId ?? crypto.randomUUID(), ...((data ?? {}) as object) },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}

export function error(code: string, message: string, status: number, requestId?: string): Response {
  return json({ error: { code, message } }, status, requestId);
}

export async function readJson<T>(request: Request): Promise<T> {
  const length = Number(request.headers.get("content-length") ?? 0);
  if (length > 16_384) throw new Response("Request body too large", { status: 413 });
  const text = await request.text();
  if (text.length > 16_384) throw new Response("Request body too large", { status: 413 });
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Response("Invalid JSON", { status: 400 });
  }
}

export function bearerToken(request: Request): string | null {
  const value = request.headers.get("authorization") ?? "";
  return value.startsWith("Bearer ") ? value.slice(7).trim() : null;
}

export async function sourceHash(request: Request, env: Env): Promise<string> {
  const source = request.headers.get("CF-Connecting-IP") ?? "unknown";
  return sha256(`${env.IP_HASH_SALT}:${source}`);
}

export async function enforceRateLimit(request: Request, env: Env, category: string, email: string): Promise<void> {
  if (!env.AUTH_RATE_LIMITER) return;
  const key = await sha256(`${category}:${email}:${await sourceHash(request, env)}`);
  const result = await env.AUTH_RATE_LIMITER.limit({ key });
  if (!result.success) throw new Response("Too many requests", { status: 429 });
}
