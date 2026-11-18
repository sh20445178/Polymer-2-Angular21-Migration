"""Runtime helpers emitted into the generated Angular app (src/app/shared/polymer-compat.ts)."""

POLYMER_COMPAT_TS = r"""import { WritableSignal } from '@angular/core';

/**
 * Small helpers that emulate Polymer's path/array mutation APIs on top of Angular signals.
 * Every helper produces new object/array references so OnPush + zoneless change detection
 * picks the change up. Prefer refactoring call sites to plain `signal.update()` over time.
 */

function splitPath(path: string | number): string[] {
  return String(path).split('.').filter((segment) => segment.length > 0);
}

function cloneContainer(value: any): any {
  if (Array.isArray(value)) {
    return [...value];
  }
  return value && typeof value === 'object' ? { ...value } : {};
}

function cloneSet(target: any, segments: string[], value: any): any {
  if (segments.length === 0) {
    return value;
  }
  const [head, ...rest] = segments;
  const copy = cloneContainer(target);
  copy[head] = cloneSet(copy[head], rest, value);
  return copy;
}

export function getPath(root: any, path: string | number): any {
  return splitPath(path).reduce((value, segment) => (value == null ? undefined : value[segment]), root);
}

export function setPath<T>(signal: WritableSignal<T>, path: string | number, value: unknown): void {
  signal.update((root) => cloneSet(root, splitPath(path), value) as T);
}

function updateArray<T, R>(signal: WritableSignal<T>, path: string | number, fn: (items: any[]) => R): R {
  let result!: R;
  signal.update((root) => {
    const segments = splitPath(path);
    const items = [...((segments.length ? getPath(root, path) : root) ?? [])];
    result = fn(items);
    return cloneSet(root, segments, items) as T;
  });
  return result;
}

export function arrayPush<T>(signal: WritableSignal<T>, path: string | number, ...items: any[]): number {
  return updateArray(signal, path, (array) => array.push(...items));
}

export function arrayPop<T>(signal: WritableSignal<T>, path: string | number): any {
  return updateArray(signal, path, (array) => array.pop());
}

export function arrayShift<T>(signal: WritableSignal<T>, path: string | number): any {
  return updateArray(signal, path, (array) => array.shift());
}

export function arrayUnshift<T>(signal: WritableSignal<T>, path: string | number, ...items: any[]): number {
  return updateArray(signal, path, (array) => array.unshift(...items));
}

export function arraySplice<T>(
  signal: WritableSignal<T>,
  path: string | number,
  start: number,
  deleteCount?: number,
  ...items: any[]
): any[] {
  return updateArray(signal, path, (array) =>
    deleteCount === undefined ? array.splice(start) : array.splice(start, deleteCount, ...items),
  );
}

/** Equivalent of Polymer's notifyPath/notifySplices: re-emit the signal with a new reference. */
export function notifyChange<T>(signal: WritableSignal<T>): void {
  signal.update((value) => cloneContainer(value) as T);
}

/** Polymer attribute serialization: true -> '', false/null/undefined -> removed, other -> String(value). */
export function toAttribute(value: unknown): string | null {
  if (value === true) {
    return '';
  }
  if (value === false || value === null || value === undefined) {
    return null;
  }
  return String(value);
}

/** Replacement for Polymer's this.debounce()/cancelDebouncer() API. */
export class Debouncer {
  private readonly timers = new Map<string, { handle: ReturnType<typeof setTimeout>; fn: () => void }>();

  debounce(name: string, fn: () => void, wait = 0): void {
    this.cancel(name);
    const handle = setTimeout(() => {
      this.timers.delete(name);
      fn();
    }, wait);
    this.timers.set(name, { handle, fn });
  }

  cancel(name: string): void {
    const timer = this.timers.get(name);
    if (timer) {
      clearTimeout(timer.handle);
      this.timers.delete(name);
    }
  }

  flush(name: string): void {
    const timer = this.timers.get(name);
    if (timer) {
      this.cancel(name);
      timer.fn();
    }
  }

  isActive(name: string): boolean {
    return this.timers.has(name);
  }

  cancelAll(): void {
    for (const name of [...this.timers.keys()]) {
      this.cancel(name);
    }
  }
}

/** Shape of the `route` object produced by Polymer's <app-location>/<app-route>. */
export interface PolymerRoute {
  prefix: string;
  path: string;
  __queryParams?: Record<string, string>;
}

export function locationToRoute(url: string): PolymerRoute {
  const [path, query = ''] = url.split('?');
  return {
    prefix: '',
    path: path.split('#')[0] || '/',
    __queryParams: Object.fromEntries(new URLSearchParams(query)),
  };
}

/** Port of <app-route pattern="/:page"> matching. */
export function matchRoute(
  pattern: string,
  route: PolymerRoute | undefined,
): { active: boolean; data: Record<string, string>; tail: PolymerRoute } {
  const path = route?.path ?? '';
  const prefix = route?.prefix ?? '';
  const patternPieces = pattern.split('/');
  const pathPieces = path.split('/');
  const data: Record<string, string> = {};
  const inactive = { active: false, data: {}, tail: { prefix: prefix, path: '' } };
  let i = 0;
  for (; i < patternPieces.length; i++) {
    const target = patternPieces[i];
    const piece = pathPieces[i];
    if (piece === undefined) {
      if (!target) {
        break;
      }
      return inactive;
    }
    if (target.startsWith(':')) {
      data[target.slice(1)] = decodeURIComponent(piece);
    } else if (target !== piece) {
      return inactive;
    }
  }
  const matched = pathPieces.slice(0, i).join('/');
  const rest = pathPieces.slice(i).join('/');
  return {
    active: true,
    data,
    tail: { prefix: prefix + matched, path: rest ? '/' + rest : '', __queryParams: route?.__queryParams },
  };
}
"""
