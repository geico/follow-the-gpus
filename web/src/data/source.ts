// The DataSource client (spec 0006 section 7.2). The only code of web/src/ that calls fetch.

import type { EmptyMeta, Meta, WindowHorizon, WindowPayload } from "../board/derive/types";

export type MetaResponse = Meta | EmptyMeta;

export interface DataSource {
  meta(): Promise<MetaResponse>;
  window(hz: WindowHorizon, page: number): Promise<WindowPayload>;
}

/** The error of a failed request: the path and the HTTP status (spec 0006 section 11.6). */
export class RequestError extends Error {
  constructor(
    readonly path: string,
    readonly status: number,
  ) {
    super(`${path} ${status}`);
    this.name = "RequestError";
  }
}

export function metaPath(): string {
  return "api/meta.json";
}

export function windowPath(hz: WindowHorizon, page: number): string {
  return `api/window/${hz}/${page}.json`;
}

type Fetch = (input: string) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>;

/**
 * Reads the 2 paths relative to the base URL of the page. Each response stays in memory for the
 * life of the page, so a return to a page sends no new request. A failed request is not kept.
 */
export class HttpDataSource implements DataSource {
  private readonly cache = new Map<string, Promise<unknown>>();

  constructor(
    private readonly base: string = import.meta.env.BASE_URL,
    private readonly fetchFn: Fetch = (u) => fetch(u),
  ) {}

  private get<T>(path: string): Promise<T> {
    const hit = this.cache.get(path);
    if (hit !== undefined) return hit as Promise<T>;
    const p = this.fetchFn(this.base + path).then(async (r) => {
      if (!r.ok) throw new RequestError(path, r.status);
      return (await r.json()) as T;
    });
    this.cache.set(path, p);
    p.catch(() => this.cache.delete(path));
    return p;
  }

  meta(): Promise<MetaResponse> {
    return this.get<MetaResponse>(metaPath());
  }

  window(hz: WindowHorizon, page: number): Promise<WindowPayload> {
    return this.get<WindowPayload>(windowPath(hz, page));
  }
}
