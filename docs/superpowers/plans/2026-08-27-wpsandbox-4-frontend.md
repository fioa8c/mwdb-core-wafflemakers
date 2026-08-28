# WP Sandbox — Stage 4: Frontend Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the "WP Sandbox" tab to the sample page: a run launcher, a polling run list, and a sectioned, text-only report viewer, with jest tests for the pure report helpers.

**Architecture:** Frontend half of the `wpsandbox` plugin package in `docker/plugins/wpsandbox/` (same dual-package layout as `phpdeobf`: `package.json` named `@mwdb-web/plugin-wpsandbox`, `index.tsx` registering `sampleTabsAfter`). Pure logic lives in `api.ts` (typed calls) and `report.ts` (summary counts, host extraction, section descriptors) so it is unit-testable without React; components are small and one-per-section. Polling is a `usePolling` hook. jest picks the plugin tests up via a new `roots` entry in `mwdb/web/jest.config.js`.

**Tech Stack:** React 18 + TypeScript, `@mwdb-web/*` aliases (vite + jest), Bootstrap classes already used by MWDB, `@fortawesome` icons, jest + ts-jest (+ `@testing-library/react` already in `mwdb/web/package.json`).

**Spec:** `docs/superpowers/specs/2026-08-27-wpsandbox-plugin-design.md` (§5.4 Frontend tab, §7 Report schema, §8 Security)

## Global Constraints

- Every piece of report content is rendered as text (`{value}` in JSX, `<pre>`, `<code>`). **No `dangerouslySetInnerHTML` anywhere in this plugin.**
- Endpoints (Stage 2): `POST /wpsandbox/<sha>` → `202 {run_id}` / `409 {run_id, message}`; `GET /wpsandbox/<sha>` → `{runs}`; `GET /wpsandbox/run/<id>`; `DELETE /wpsandbox/run/<id>`. The axios instance is `api.axios` from `APIContext` with base path `/api` already applied.
- Report blob is fetched with `api.getObject("blob", report_blob_id)` → `.data.content` (string JSON).
- Polling interval 3 s while any run is `queued|running`; stops otherwise.
- Launcher disabled when the user lacks `Capability.addingBlobs` (`useCheckCapabilities().userHasCapabilities`).
- Default `path` = `wp-content/uploads/<sha256[:8]>.php`; default `timeout` 120; max timeout 300.
- `npx prettier --check src` (from `mwdb/web`) is CI; run `npx prettier --write ../../docker/plugins/wpsandbox` before committing. `npx tsc` must pass in the web container.

---

## File map

```
docker/plugins/wpsandbox/
├── package.json
├── tsconfig.json
├── index.tsx                       # sampleTabsAfter registration
├── api.ts                          # types + createRun/listRuns/getRun/cancelRun/getReport
├── report.ts                       # pure helpers: summarize(), hosts(), sections()
├── hooks/usePolling.ts
├── components/
│   ├── WpSandboxTab.tsx            # composition: launcher + run list + report view
│   ├── RunLauncher.tsx
│   ├── RunList.tsx
│   ├── ReportView.tsx              # collapsible sections, badges, truncation notice
│   ├── Section.tsx                 # generic collapsible with count badge
│   ├── sections/NetworkSection.tsx
│   ├── sections/FilesystemSection.tsx
│   ├── sections/DatabaseSection.tsx
│   ├── sections/PhpSection.tsx
│   └── sections/TriggerSection.tsx
└── __tests__/
    ├── fixtures/report_sample.json  # copy of docker/wpsandbox-worker/tests/fixtures/report_sample.json
    ├── report.test.ts
    └── ReportView.test.tsx
mwdb/web/jest.config.js             # + roots / testMatch for docker/plugins
```

---

### Task 1: Package scaffold, types, API client, jest wiring

**Files:**
- Create: `docker/plugins/wpsandbox/package.json`, `tsconfig.json`
- Create: `docker/plugins/wpsandbox/api.ts`
- Create: `docker/plugins/wpsandbox/__tests__/fixtures/report_sample.json` (copy of the worker fixture)
- Modify: `mwdb/web/jest.config.js`
- Test: `docker/plugins/wpsandbox/__tests__/api.test.ts`

**Interfaces:**
- Produces (in `api.ts`):
  ```ts
  export type RunStatus = "queued" | "running" | "done" | "failed" | "timeout";
  export type RunMode = "webroot" | "plugin";
  export type Run = { id: string; object_id: number; requested_by: number | null; mode: RunMode;
                      params: Record<string, unknown>; status: RunStatus; created_at: string;
                      started_at: string | null; finished_at: string | null; error: string | null;
                      report_blob_id: string | null; sandbox_id: string | null; sample_sha256: string };
  export type CreateRunBody = { mode: RunMode; path?: string; method?: "GET" | "POST"; query?: string;
                                body?: string; timeout?: number };
  export type Report = { … §7 shape … };
  export function isActive(run: Run): boolean;
  export async function createRun(axios, sha, body): Promise<{ run_id: string; existing: boolean }>;  // 409 → existing:true
  export async function listRuns(axios, sha): Promise<Run[]>;
  export async function getRun(axios, runId): Promise<Run>;
  export async function cancelRun(axios, runId): Promise<void>;
  export function defaultPath(sha: string): string;
  ```

- [ ] **Step 1: package.json, tsconfig, jest roots**

`docker/plugins/wpsandbox/package.json`:
```json
{
    "name": "@mwdb-web/plugin-wpsandbox",
    "version": "0.1.0",
    "description": "WordPress sandbox (SecEx) tab for the MWDB sample page",
    "main": "./index.tsx",
    "private": true
}
```

`docker/plugins/wpsandbox/tsconfig.json` — copy `docker/plugins/phpdeobf/tsconfig.json` verbatim.

`mwdb/web/jest.config.js` — add `roots` and a plugin `testMatch` entry:
```js
module.exports = {
    preset: "ts-jest",
    testEnvironment: "jsdom",
    roots: ["<rootDir>/src", "<rootDir>/../../docker/plugins"],
    transform: {
        "^.+\\.(js|jsx|ts|tsx)$": "ts-jest",
    },
    testMatch: [
        "<rootDir>/src/**/__tests__/**/*.{js,jsx,ts,tsx}",
        "<rootDir>/src/**/*.{spec,test}.{js,jsx,ts,tsx}",
        "<rootDir>/../../docker/plugins/**/__tests__/**/*.{test,spec}.{ts,tsx}",
    ],
    moduleFileExtensions: ["js", "jsx", "ts", "tsx", "json"],
    moduleNameMapper: {
        "^@/(.*)$": "<rootDir>/src/$1",
        "^@mwdb-web/commons/(.*)$": "<rootDir>/src/commons/$1",
        "^@mwdb-web/components/(.*)$": "<rootDir>/src/components/$1",
        "^@mwdb-web/types/(.*)$": "<rootDir>/src/types/$1",
        "^@mwdb-web/plugins$": "<rootDir>/src/mocks/plugins.ts",
    },
    resetMocks: true,
};
```
(The original file declared `moduleNameMapper` twice — the second silently overrode the first; this merges them. Keep behaviour identical for existing tests: run `npm run test` from `mwdb/web` before and after and compare the pass count.)

Copy the fixture: `cp docker/wpsandbox-worker/tests/fixtures/report_sample.json docker/plugins/wpsandbox/__tests__/fixtures/report_sample.json` (if Stage 3 is not merged yet, paste the JSON from Stage 3 plan Task 1 Step 5).

- [ ] **Step 2: Write the failing test**

`docker/plugins/wpsandbox/__tests__/api.test.ts`:
```ts
import { AxiosInstance } from "axios";

import { cancelRun, createRun, defaultPath, getRun, isActive, listRuns, Run } from "../api";

const SHA = "ab".repeat(32);

function fakeAxios(impl: Partial<Record<"get" | "post" | "delete", jest.Mock>>) {
    return { get: jest.fn(), post: jest.fn(), delete: jest.fn(), ...impl } as unknown as AxiosInstance;
}

const run = (over: Partial<Run> = {}): Run => ({
    id: "r1", object_id: 1, requested_by: 1, mode: "webroot", params: {}, status: "queued",
    created_at: "2026-08-27T10:00:00+00:00", started_at: null, finished_at: null, error: null,
    report_blob_id: null, sandbox_id: null, sample_sha256: SHA, ...over,
});

test("defaultPath uses first 8 chars of sha", () => {
    expect(defaultPath(SHA)).toBe("wp-content/uploads/abababab.php");
});

test("isActive", () => {
    expect(isActive(run({ status: "queued" }))).toBe(true);
    expect(isActive(run({ status: "running" }))).toBe(true);
    expect(isActive(run({ status: "done" }))).toBe(false);
    expect(isActive(run({ status: "failed" }))).toBe(false);
});

test("createRun returns run_id on 202", async () => {
    const post = jest.fn().mockResolvedValue({ status: 202, data: { run_id: "r9" } });
    const res = await createRun(fakeAxios({ post }), SHA, { mode: "webroot", timeout: 10 });
    expect(res).toEqual({ run_id: "r9", existing: false });
    expect(post).toHaveBeenCalledWith(`/wpsandbox/${SHA}`, { mode: "webroot", timeout: 10 });
});

test("createRun maps 409 to existing:true", async () => {
    const post = jest.fn().mockRejectedValue({ response: { status: 409, data: { run_id: "r1" } } });
    expect(await createRun(fakeAxios({ post }), SHA, { mode: "plugin" })).toEqual({ run_id: "r1", existing: true });
});

test("createRun rethrows other errors", async () => {
    const post = jest.fn().mockRejectedValue({ response: { status: 400, data: { message: "bad" } } });
    await expect(createRun(fakeAxios({ post }), SHA, { mode: "webroot" })).rejects.toBeTruthy();
});

test("listRuns / getRun / cancelRun hit the right URLs", async () => {
    const get = jest.fn()
        .mockResolvedValueOnce({ data: { runs: [run()] } })
        .mockResolvedValueOnce({ data: run({ id: "r2" }) });
    const del = jest.fn().mockResolvedValue({ data: {} });
    const ax = fakeAxios({ get, delete: del });
    expect((await listRuns(ax, SHA))[0].id).toBe("r1");
    expect((await getRun(ax, "r2")).id).toBe("r2");
    await cancelRun(ax, "r2");
    expect(get).toHaveBeenNthCalledWith(1, `/wpsandbox/${SHA}`);
    expect(get).toHaveBeenNthCalledWith(2, `/wpsandbox/run/r2`);
    expect(del).toHaveBeenCalledWith(`/wpsandbox/run/r2`);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox/__tests__/api.test.ts`
Expected: FAIL — cannot find module `../api`

- [ ] **Step 4: Implement api.ts**

`docker/plugins/wpsandbox/api.ts`:
```ts
import { AxiosInstance } from "axios";

export type RunStatus = "queued" | "running" | "done" | "failed" | "timeout";
export type RunMode = "webroot" | "plugin";

export type Run = {
    id: string;
    object_id: number;
    requested_by: number | null;
    mode: RunMode;
    params: Record<string, unknown>;
    status: RunStatus;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    error: string | null;
    report_blob_id: string | null;
    sandbox_id: string | null;
    sample_sha256: string;
};

export type CreateRunBody = {
    mode: RunMode;
    path?: string;
    method?: "GET" | "POST";
    query?: string;
    body?: string;
    timeout?: number;
};

export type TriggerRequest = {
    url: string; method: string; status: number; response_head: string; elapsed_ms: number;
    error?: string | null;
};
export type CreatedFile = { path: string; size: number; mode: string; sha256: string; content: string | null };
export type ModifiedFile = { path: string; sha256_before: string; sha256_after: string; diff: string | null };
export type FsEvent = { ts: string; op: string; path: string; pid: number };
export type DnsQuery = { ts: string; name: string; answers: string[] };
export type Flow = {
    ts: string; method: string; url: string; status: number;
    request_headers: Record<string, string>; request_body: string;
    response_headers: Record<string, string>; response_body: string; response_sha256: string;
};
export type EvalLayer = { depth: number; sha256: string; code: string };

export type Report = {
    schema_version: number;
    run: { mode: RunMode; params: Record<string, unknown>; template: string; wp_version: string;
           php_version: string; started_at: string; duration_s: number };
    trigger: { requests: TriggerRequest[] };
    filesystem: { created: CreatedFile[]; modified: ModifiedFile[]; deleted: { path: string }[]; events: FsEvent[] };
    database: {
        users: { added: Record<string, unknown>[]; modified: Record<string, unknown>[] };
        options: { added: Record<string, unknown>[]; modified: { name: string; before: string; after: string }[] };
        cron: { added: Record<string, unknown>[] };
        posts: { added: Record<string, unknown>[]; modified: Record<string, unknown>[] };
        other_tables_changed: string[];
    };
    network: { dns: DnsQuery[]; flows: Flow[] };
    php: { eval_layers: EvalLayer[]; errors: string[] };
    truncated: string[];
};

export const ACTIVE: RunStatus[] = ["queued", "running"];
export const DEFAULT_TIMEOUT = 120;
export const MAX_TIMEOUT = 300;

export function isActive(run: Run): boolean {
    return ACTIVE.includes(run.status);
}

export function defaultPath(sha256: string): string {
    return `wp-content/uploads/${sha256.slice(0, 8)}.php`;
}

export async function createRun(
    axios: AxiosInstance, sha256: string, body: CreateRunBody,
): Promise<{ run_id: string; existing: boolean }> {
    try {
        const resp = await axios.post<{ run_id: string }>(`/wpsandbox/${sha256}`, body);
        return { run_id: resp.data.run_id, existing: false };
    } catch (e: unknown) {
        const err = e as { response?: { status?: number; data?: { run_id?: string } } };
        if (err.response?.status === 409 && err.response.data?.run_id) {
            return { run_id: err.response.data.run_id, existing: true };
        }
        throw e;
    }
}

export async function listRuns(axios: AxiosInstance, sha256: string): Promise<Run[]> {
    const resp = await axios.get<{ runs: Run[] }>(`/wpsandbox/${sha256}`);
    return resp.data.runs;
}

export async function getRun(axios: AxiosInstance, runId: string): Promise<Run> {
    const resp = await axios.get<Run>(`/wpsandbox/run/${runId}`);
    return resp.data;
}

export async function cancelRun(axios: AxiosInstance, runId: string): Promise<void> {
    await axios.delete(`/wpsandbox/run/${runId}`);
}

export function errorMessage(e: unknown, fallback = "Request failed."): string {
    const err = e as { response?: { data?: { message?: string } }; message?: string };
    return err.response?.data?.message ?? err.message ?? fallback;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox/__tests__/api.test.ts`
Expected: 6 passed. Also run `npm run test` and confirm the pre-existing suite still passes with the merged jest config.

- [ ] **Step 6: Commit**

```bash
git add docker/plugins/wpsandbox/package.json docker/plugins/wpsandbox/tsconfig.json docker/plugins/wpsandbox/api.ts docker/plugins/wpsandbox/__tests__ mwdb/web/jest.config.js
git commit -m "wpsandbox FE: package scaffold, typed API client, jest wiring for plugin tests"
```

---

### Task 2: Pure report helpers

**Files:**
- Create: `docker/plugins/wpsandbox/report.ts`
- Test: `docker/plugins/wpsandbox/__tests__/report.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export type Summary = { filesCreated: number; filesModified: number; filesDeleted: number;
                          hosts: string[]; flows: number; usersAdded: string[]; optionsChanged: number;
                          cronAdded: number; evalLayers: number; phpErrors: number; truncated: number };
  export function summarize(report: Report): Summary;
  export function hosts(report: Report): string[];           // same rule as worker extract.hosts
  export function parseReport(content: string): Report | null;  // JSON.parse + schema_version check
  export function formatBytes(n: number): string;              // "1.2 KB"
  export function shortHash(h: string): string;                // 8…4
  ```

- [ ] **Step 1: Write the failing test**

`docker/plugins/wpsandbox/__tests__/report.test.ts`:
```ts
import report from "./fixtures/report_sample.json";
import { formatBytes, hosts, parseReport, shortHash, summarize } from "../report";
import type { Report } from "../api";

const R = report as unknown as Report;

test("hosts from dns and flow urls, no ports, sorted unique", () => {
    expect(hosts(R)).toEqual(["cdn.example.net", "evil.test"]);
});

test("summarize counts", () => {
    expect(summarize(R)).toEqual({
        filesCreated: 2, filesModified: 1, filesDeleted: 0,
        hosts: ["cdn.example.net", "evil.test"], flows: 2,
        usersAdded: ["wp_backup"], optionsChanged: 1, cronAdded: 0,
        evalLayers: 1, phpErrors: 0, truncated: 1,
    });
});

test("parseReport accepts v1 and rejects garbage", () => {
    expect(parseReport(JSON.stringify(R))?.schema_version).toBe(1);
    expect(parseReport("not json")).toBeNull();
    expect(parseReport(JSON.stringify({ schema_version: 99 }))).toBeNull();
});

test("formatBytes / shortHash", () => {
    expect(formatBytes(40)).toBe("40 B");
    expect(formatBytes(300000)).toBe("293.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(shortHash("ab".repeat(32))).toBe("abababab…abab");
    expect(shortHash("short")).toBe("short");
});
```

Add `"resolveJsonModule": true` to the plugin `tsconfig.json` `compilerOptions` if the import of the JSON fixture fails under ts-jest.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox/__tests__/report.test.ts`
Expected: FAIL — cannot find module `../report`

- [ ] **Step 3: Implement**

`docker/plugins/wpsandbox/report.ts`:
```ts
import type { Report } from "./api";

export type Summary = {
    filesCreated: number;
    filesModified: number;
    filesDeleted: number;
    hosts: string[];
    flows: number;
    usersAdded: string[];
    optionsChanged: number;
    cronAdded: number;
    evalLayers: number;
    phpErrors: number;
    truncated: number;
};

function hostOf(url: string): string | null {
    try {
        return new URL(url).hostname || null;
    } catch {
        return null;
    }
}

export function hosts(report: Report): string[] {
    const out = new Set<string>();
    for (const q of report.network?.dns ?? []) if (q.name) out.add(q.name.replace(/\.$/, ""));
    for (const f of report.network?.flows ?? []) {
        const h = hostOf(f.url);
        if (h) out.add(h);
    }
    return Array.from(out).sort();
}

export function summarize(report: Report): Summary {
    const fs = report.filesystem ?? { created: [], modified: [], deleted: [], events: [] };
    const db = report.database;
    return {
        filesCreated: fs.created?.length ?? 0,
        filesModified: fs.modified?.length ?? 0,
        filesDeleted: fs.deleted?.length ?? 0,
        hosts: hosts(report),
        flows: report.network?.flows?.length ?? 0,
        usersAdded: (db?.users?.added ?? []).map((u) => String(u.user_login ?? "")).filter(Boolean),
        optionsChanged: (db?.options?.added?.length ?? 0) + (db?.options?.modified?.length ?? 0),
        cronAdded: db?.cron?.added?.length ?? 0,
        evalLayers: report.php?.eval_layers?.length ?? 0,
        phpErrors: report.php?.errors?.length ?? 0,
        truncated: report.truncated?.length ?? 0,
    };
}

export function parseReport(content: string): Report | null {
    try {
        const obj = JSON.parse(content);
        if (obj && obj.schema_version === 1) return obj as Report;
        return null;
    } catch {
        return null;
    }
}

export function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function shortHash(h: string): string {
    return h.length > 12 ? `${h.slice(0, 8)}…${h.slice(-4)}` : h;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox/__tests__/report.test.ts`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add docker/plugins/wpsandbox/report.ts docker/plugins/wpsandbox/__tests__/report.test.ts docker/plugins/wpsandbox/tsconfig.json
git commit -m "wpsandbox FE: pure report helpers"
```

---

### Task 3: Polling hook + run launcher + run list

**Files:**
- Create: `docker/plugins/wpsandbox/hooks/usePolling.ts`
- Create: `docker/plugins/wpsandbox/components/RunLauncher.tsx`
- Create: `docker/plugins/wpsandbox/components/RunList.tsx`
- Test: `docker/plugins/wpsandbox/__tests__/usePolling.test.tsx`

**Interfaces:**
- Produces: `usePolling(fn: () => Promise<boolean>, intervalMs: number, deps: unknown[])` — calls `fn` immediately and then every `intervalMs` **while `fn` resolves `true`**; stops when it resolves `false`; restarts when `deps` change; exposes `{ refresh(): void }`.
- Produces: `<RunLauncher sha256 fileName canLaunch busy onLaunch(body: CreateRunBody) />`; `<RunList runs selectedId onSelect(id) onCancel(id) />`.

- [ ] **Step 1: Write the failing hook test**

`docker/plugins/wpsandbox/__tests__/usePolling.test.tsx`:
```tsx
import { act, renderHook } from "@testing-library/react";

import { usePolling } from "../hooks/usePolling";

jest.useFakeTimers();

test("polls while fn returns true, stops when false", async () => {
    const results = [true, true, false];
    const fn = jest.fn().mockImplementation(() => Promise.resolve(results.shift() ?? false));
    renderHook(() => usePolling(fn, 1000, []));
    await act(async () => {});                       // initial call
    expect(fn).toHaveBeenCalledTimes(1);
    await act(async () => { jest.advanceTimersByTime(1000); });
    expect(fn).toHaveBeenCalledTimes(2);
    await act(async () => { jest.advanceTimersByTime(1000); });
    expect(fn).toHaveBeenCalledTimes(3);            // returned false -> stop
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(fn).toHaveBeenCalledTimes(3);
});

test("refresh triggers an immediate call and resumes", async () => {
    const fn = jest.fn().mockResolvedValue(false);
    const { result } = renderHook(() => usePolling(fn, 1000, []));
    await act(async () => {});
    expect(fn).toHaveBeenCalledTimes(1);
    await act(async () => { result.current.refresh(); });
    expect(fn).toHaveBeenCalledTimes(2);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox/__tests__/usePolling.test.tsx`
Expected: FAIL — cannot find module

- [ ] **Step 3: Implement the hook**

`docker/plugins/wpsandbox/hooks/usePolling.ts`:
```ts
import { useCallback, useEffect, useRef } from "react";

/** Calls `fn` now and every `intervalMs` while it resolves true. */
export function usePolling(
    fn: () => Promise<boolean>,
    intervalMs: number,
    deps: unknown[],
): { refresh: () => void } {
    const fnRef = useRef(fn);
    fnRef.current = fn;
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const alive = useRef(true);

    const tick = useCallback(async () => {
        if (timer.current) {
            clearTimeout(timer.current);
            timer.current = null;
        }
        let again = false;
        try {
            again = await fnRef.current();
        } catch {
            again = true; // transient error: keep polling
        }
        if (alive.current && again) {
            timer.current = setTimeout(tick, intervalMs);
        }
    }, [intervalMs]);

    useEffect(() => {
        alive.current = true;
        void tick();
        return () => {
            alive.current = false;
            if (timer.current) clearTimeout(timer.current);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tick, ...deps]);

    return { refresh: () => void tick() };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox/__tests__/usePolling.test.tsx`
Expected: 2 passed

- [ ] **Step 5: RunLauncher and RunList**

`docker/plugins/wpsandbox/components/RunLauncher.tsx`:
```tsx
import { useState } from "react";

import { CreateRunBody, DEFAULT_TIMEOUT, MAX_TIMEOUT, RunMode, defaultPath } from "../api";

type Props = {
    sha256: string;
    fileName?: string;
    canLaunch: boolean;
    busy: boolean;
    onLaunch: (body: CreateRunBody) => void;
};

export function RunLauncher({ sha256, fileName, canLaunch, busy, onLaunch }: Props) {
    const isZip = /\.zip$/i.test(fileName ?? "");
    const [mode, setMode] = useState<RunMode>(isZip ? "plugin" : "webroot");
    const [path, setPath] = useState(defaultPath(sha256));
    const [method, setMethod] = useState<"GET" | "POST">("GET");
    const [query, setQuery] = useState("");
    const [body, setBody] = useState("");
    const [timeout, setTimeoutS] = useState(DEFAULT_TIMEOUT);

    const disabled = !canLaunch || busy || (mode === "plugin" && !isZip);

    function submit() {
        const base: CreateRunBody = { mode, timeout };
        onLaunch(mode === "webroot" ? { ...base, path, method, query, body } : base);
    }

    return (
        <div className="card mb-3">
            <div className="card-body">
                <div className="form-row align-items-end">
                    <div className="form-group col-md-2">
                        <label>Mode</label>
                        <select className="form-control" value={mode} onChange={(e) => setMode(e.target.value as RunMode)}>
                            <option value="webroot">Webroot + HTTP</option>
                            <option value="plugin">Install as plugin</option>
                        </select>
                    </div>
                    {mode === "webroot" && (
                        <>
                            <div className="form-group col-md-4">
                                <label>Path (relative to WP root)</label>
                                <input className="form-control" value={path} onChange={(e) => setPath(e.target.value)} />
                            </div>
                            <div className="form-group col-md-1">
                                <label>Method</label>
                                <select className="form-control" value={method} onChange={(e) => setMethod(e.target.value as "GET" | "POST")}>
                                    <option>GET</option>
                                    <option>POST</option>
                                </select>
                            </div>
                            <div className="form-group col-md-2">
                                <label>Query</label>
                                <input className="form-control" placeholder="a=1&b=2" value={query} onChange={(e) => setQuery(e.target.value)} />
                            </div>
                            <div className="form-group col-md-2">
                                <label>Body</label>
                                <input className="form-control" value={body} onChange={(e) => setBody(e.target.value)} disabled={method !== "POST"} />
                            </div>
                        </>
                    )}
                    <div className="form-group col-md-1">
                        <label>Timeout (s)</label>
                        <input type="number" className="form-control" min={1} max={MAX_TIMEOUT} value={timeout}
                               onChange={(e) => setTimeoutS(Math.min(MAX_TIMEOUT, Math.max(1, Number(e.target.value) || 1)))} />
                    </div>
                </div>
                {mode === "plugin" && !isZip && (
                    <div className="text-warning small mb-2">Plugin mode needs a .zip sample.</div>
                )}
                <button type="button" className="btn btn-primary" disabled={disabled} onClick={submit}
                        title={!canLaunch ? "You need the adding_blobs capability to run the sandbox" : undefined}>
                    {busy ? "Queuing…" : "Run in WordPress sandbox"}
                </button>
            </div>
        </div>
    );
}
```

`docker/plugins/wpsandbox/components/RunList.tsx`:
```tsx
import { Run, isActive } from "../api";

type Props = {
    runs: Run[];
    selectedId: string | null;
    onSelect: (id: string) => void;
    onCancel: (id: string) => void;
};

function duration(run: Run): string {
    if (!run.started_at) return "";
    const end = run.finished_at ? new Date(run.finished_at) : new Date();
    const s = Math.max(0, Math.round((end.getTime() - new Date(run.started_at).getTime()) / 1000));
    return `${s}s`;
}

const BADGE: Record<Run["status"], string> = {
    queued: "secondary", running: "info", done: "success", failed: "danger", timeout: "warning",
};

export function RunList({ runs, selectedId, onSelect, onCancel }: Props) {
    if (runs.length === 0) return <p className="text-muted">No sandbox runs yet.</p>;
    return (
        <table className="table table-sm table-hover">
            <thead>
                <tr><th>Started</th><th>Mode</th><th>Status</th><th>Duration</th><th /></tr>
            </thead>
            <tbody>
                {runs.map((r) => (
                    <tr key={r.id} className={r.id === selectedId ? "table-active" : ""}
                        style={{ cursor: "pointer" }} onClick={() => onSelect(r.id)}>
                        <td>{new Date(r.created_at).toLocaleString()}</td>
                        <td>{r.mode}</td>
                        <td>
                            <span className={`badge badge-${BADGE[r.status]}`}>
                                {isActive(r) && <span className="spinner-border spinner-border-sm mr-1" />}
                                {r.status}
                            </span>
                        </td>
                        <td>{duration(r)}</td>
                        <td>
                            {r.status === "queued" && (
                                <button type="button" className="btn btn-link btn-sm p-0"
                                        onClick={(e) => { e.stopPropagation(); onCancel(r.id); }}>
                                    cancel
                                </button>
                            )}
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
```

- [ ] **Step 6: Type-check and commit**

Run: `cd mwdb/web && npx tsc --noEmit -p ../../docker/plugins/wpsandbox/tsconfig.json` (if the plugin tsconfig doesn't resolve `@mwdb-web/*` outside the container, run `docker compose -f docker-compose-dev.yml exec mwdb-web npx tsc` instead).
Expected: no errors.

```bash
git add docker/plugins/wpsandbox/hooks docker/plugins/wpsandbox/components/RunLauncher.tsx docker/plugins/wpsandbox/components/RunList.tsx docker/plugins/wpsandbox/__tests__/usePolling.test.tsx
git commit -m "wpsandbox FE: polling hook, run launcher, run list"
```

---

### Task 4: Report view + sections

**Files:**
- Create: `docker/plugins/wpsandbox/components/Section.tsx`
- Create: `docker/plugins/wpsandbox/components/sections/{NetworkSection,FilesystemSection,DatabaseSection,PhpSection,TriggerSection}.tsx`
- Create: `docker/plugins/wpsandbox/components/ReportView.tsx`
- Test: `docker/plugins/wpsandbox/__tests__/ReportView.test.tsx`

**Interfaces:**
- Produces: `<ReportView report={Report} blobId={string} />` rendering, in order: Summary chips + truncation notice → Network → Filesystem → Database → PHP → Trigger → Raw JSON link (`/blob/<blobId>`). Every section is `<Section title count defaultOpen>`.

- [ ] **Step 1: Write the failing test**

`docker/plugins/wpsandbox/__tests__/ReportView.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import fixture from "./fixtures/report_sample.json";
import { ReportView } from "../components/ReportView";
import type { Report } from "../api";

const R = fixture as unknown as Report;

function renderView(report: Report = R) {
    return render(<MemoryRouter><ReportView report={report} blobId={"ef".repeat(32)} /></MemoryRouter>);
}

test("summary chips show counts and hosts", () => {
    renderView();
    expect(screen.getByText("2 files created")).toBeTruthy();
    expect(screen.getByText("1 file modified")).toBeTruthy();
    expect(screen.getByText("2 hosts contacted")).toBeTruthy();
    expect(screen.getByText("1 user added")).toBeTruthy();
    expect(screen.getByText("1 eval layer")).toBeTruthy();
});

test("truncation notice lists paths", () => {
    renderView();
    expect(screen.getByText(/1 field was truncated/)).toBeTruthy();
    expect(screen.getByText("filesystem.created[1].content")).toBeTruthy();
});

test("section headers carry count badges", () => {
    renderView();
    expect(screen.getByRole("button", { name: /Network.*3/ })).toBeTruthy();     // 1 dns + 2 flows
    expect(screen.getByRole("button", { name: /Filesystem.*3/ })).toBeTruthy();  // 2 created + 1 modified
    expect(screen.getByRole("button", { name: /Database.*3/ })).toBeTruthy();    // 1 user + 1 option + 1 other table
    expect(screen.getByRole("button", { name: /PHP.*1/ })).toBeTruthy();
});

test("captured html is rendered as text, never as markup", () => {
    const evil = JSON.parse(JSON.stringify(R)) as Report;
    evil.filesystem.created[0].content = "<img src=x onerror=alert(1)>";
    evil.network.flows[0].response_body = "<script>alert(2)</script>";
    const { container } = renderView(evil);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeTruthy();
});

test("empty report shows empty states and no truncation notice", () => {
    const empty: Report = {
        ...R,
        filesystem: { created: [], modified: [], deleted: [], events: [] },
        database: { users: { added: [], modified: [] }, options: { added: [], modified: [] },
                    cron: { added: [] }, posts: { added: [], modified: [] }, other_tables_changed: [] },
        network: { dns: [], flows: [] }, php: { eval_layers: [], errors: [] }, truncated: [],
    };
    renderView(empty);
    expect(screen.queryByText(/truncated/)).toBeNull();
    expect(screen.getByText("No network activity captured.")).toBeTruthy();
    expect(screen.getByText("No filesystem changes.")).toBeTruthy();
});

test("raw json link points to the blob", () => {
    renderView();
    const link = screen.getByRole("link", { name: /Raw JSON/ }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(`/blob/${"ef".repeat(32)}`);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox/__tests__/ReportView.test.tsx`
Expected: FAIL — cannot find module

- [ ] **Step 3: Section + section components**

`docker/plugins/wpsandbox/components/Section.tsx`:
```tsx
import { ReactNode, useState } from "react";

type Props = { title: string; count: number; defaultOpen?: boolean; children: ReactNode };

export function Section({ title, count, defaultOpen = true, children }: Props) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="card mb-2">
            <button type="button" className="card-header text-left btn btn-light d-flex justify-content-between"
                    onClick={() => setOpen(!open)} aria-expanded={open}>
                <span>{title}</span>
                <span className={`badge badge-${count > 0 ? "primary" : "secondary"}`}>{count}</span>
            </button>
            {open && <div className="card-body">{children}</div>}
        </div>
    );
}

export function Code({ children }: { children: string }) {
    return (
        <pre className="border rounded p-2 bg-light" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 400, overflow: "auto" }}>
            {children}
        </pre>
    );
}
```

`docker/plugins/wpsandbox/components/sections/NetworkSection.tsx`:
```tsx
import { useState } from "react";

import { Report } from "../../api";
import { Code, Section } from "../Section";

export function NetworkSection({ report }: { report: Report }) {
    const { dns, flows } = report.network;
    const [openFlow, setOpenFlow] = useState<number | null>(null);
    const count = dns.length + flows.length;
    return (
        <Section title="Network" count={count}>
            {count === 0 && <p className="text-muted mb-0">No network activity captured.</p>}
            {dns.length > 0 && (
                <>
                    <h6>DNS queries</h6>
                    <table className="table table-sm">
                        <tbody>
                            {dns.map((q, i) => (
                                <tr key={i}><td className="text-monospace">{q.name}</td><td>{q.answers.join(", ") || "—"}</td><td className="text-muted">{q.ts}</td></tr>
                            ))}
                        </tbody>
                    </table>
                </>
            )}
            {flows.length > 0 && (
                <>
                    <h6>HTTP flows</h6>
                    <table className="table table-sm">
                        <tbody>
                            {flows.map((f, i) => (
                                <tr key={i} style={{ cursor: "pointer" }} onClick={() => setOpenFlow(openFlow === i ? null : i)}>
                                    <td>{f.method}</td>
                                    <td className="text-monospace" style={{ wordBreak: "break-all" }}>{f.url}</td>
                                    <td>{f.status}</td>
                                    <td className="text-muted">{f.ts}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {openFlow !== null && flows[openFlow] && (
                        <div>
                            <h6>Request body</h6>
                            <Code>{flows[openFlow].request_body || "(empty)"}</Code>
                            <h6>Response body <small className="text-muted">sha256 {flows[openFlow].response_sha256}</small></h6>
                            <Code>{flows[openFlow].response_body || "(empty)"}</Code>
                        </div>
                    )}
                </>
            )}
        </Section>
    );
}
```

`docker/plugins/wpsandbox/components/sections/FilesystemSection.tsx`:
```tsx
import { Report } from "../../api";
import { formatBytes } from "../../report";
import { Code, Section } from "../Section";

export function FilesystemSection({ report }: { report: Report }) {
    const { created, modified, deleted, events } = report.filesystem;
    const count = created.length + modified.length + deleted.length;
    return (
        <Section title="Filesystem" count={count}>
            {count === 0 && <p className="text-muted mb-0">No filesystem changes.</p>}
            {created.map((f) => (
                <div key={f.path} className="mb-3">
                    <div><span className="badge badge-success mr-2">created</span>
                        <span className="text-monospace">{f.path}</span>
                        <small className="text-muted ml-2">{formatBytes(f.size)} · mode {f.mode} · sha256 {f.sha256}</small></div>
                    {f.content !== null ? <Code>{f.content}</Code>
                        : <p className="text-muted small mb-0">Content exceeded the inline limit — uploaded as a child file of this sample.</p>}
                </div>
            ))}
            {modified.map((f) => (
                <div key={f.path} className="mb-3">
                    <div><span className="badge badge-warning mr-2">modified</span><span className="text-monospace">{f.path}</span></div>
                    {f.diff ? <Code>{f.diff}</Code> : <p className="text-muted small mb-0">Binary or oversized — no diff.</p>}
                </div>
            ))}
            {deleted.map((f) => (
                <div key={f.path}><span className="badge badge-danger mr-2">deleted</span><span className="text-monospace">{f.path}</span></div>
            ))}
            {events.length > 0 && (
                <details className="mt-2">
                    <summary>{events.length} raw audit events</summary>
                    <table className="table table-sm mt-2"><tbody>
                        {events.map((e, i) => <tr key={i}><td>{e.ts}</td><td>{e.op}</td><td className="text-monospace">{e.path}</td><td>pid {e.pid}</td></tr>)}
                    </tbody></table>
                </details>
            )}
        </Section>
    );
}
```

`docker/plugins/wpsandbox/components/sections/DatabaseSection.tsx`:
```tsx
import { Report } from "../../api";
import { Code, Section } from "../Section";

function Rows({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
    if (rows.length === 0) return null;
    return (
        <>
            <h6>{title}</h6>
            <Code>{rows.map((r) => JSON.stringify(r)).join("\n")}</Code>
        </>
    );
}

export function DatabaseSection({ report }: { report: Report }) {
    const db = report.database;
    const count = db.users.added.length + db.users.modified.length + db.options.added.length
        + db.options.modified.length + db.cron.added.length + db.posts.added.length
        + db.posts.modified.length + db.other_tables_changed.length;
    return (
        <Section title="Database" count={count}>
            {count === 0 && <p className="text-muted mb-0">No WordPress state changes.</p>}
            <Rows title="Users added" rows={db.users.added} />
            <Rows title="Users modified" rows={db.users.modified} />
            {db.options.modified.length > 0 && (
                <>
                    <h6>Options modified</h6>
                    <table className="table table-sm"><tbody>
                        {db.options.modified.map((o) => <tr key={o.name}><td className="text-monospace">{o.name}</td><td>{o.before}</td><td>→</td><td>{o.after}</td></tr>)}
                    </tbody></table>
                </>
            )}
            <Rows title="Options added" rows={db.options.added} />
            <Rows title="Cron events added" rows={db.cron.added} />
            <Rows title="Posts added" rows={db.posts.added} />
            <Rows title="Posts modified" rows={db.posts.modified} />
            {db.other_tables_changed.length > 0 && (
                <p className="mb-0">Other tables changed: <span className="text-monospace">{db.other_tables_changed.join(", ")}</span></p>
            )}
        </Section>
    );
}
```

`docker/plugins/wpsandbox/components/sections/PhpSection.tsx`:
```tsx
import { Report } from "../../api";
import { Code, Section } from "../Section";

export function PhpSection({ report }: { report: Report }) {
    const { eval_layers, errors } = report.php;
    return (
        <Section title="PHP" count={eval_layers.length}>
            {eval_layers.length === 0 && <p className="text-muted mb-0">No eval() layers captured.</p>}
            {eval_layers.map((l, i) => (
                <div key={i} className="mb-2">
                    <div>eval layer {l.depth} <small className="text-muted">sha256 {l.sha256}</small></div>
                    <Code>{l.code}</Code>
                </div>
            ))}
            {errors.length > 0 && (
                <details><summary>{errors.length} PHP errors/warnings</summary><Code>{errors.join("\n")}</Code></details>
            )}
        </Section>
    );
}
```

`docker/plugins/wpsandbox/components/sections/TriggerSection.tsx`:
```tsx
import { Report } from "../../api";
import { Code, Section } from "../Section";

export function TriggerSection({ report }: { report: Report }) {
    const reqs = report.trigger.requests;
    return (
        <Section title="Trigger requests" count={reqs.length} defaultOpen={false}>
            {reqs.map((r, i) => (
                <div key={i} className="mb-2">
                    <div>{r.method} <span className="text-monospace">{r.url}</span> → {r.status} <small className="text-muted">{r.elapsed_ms} ms</small>
                        {r.error && <span className="text-danger ml-2">{r.error}</span>}</div>
                    <Code>{r.response_head || "(empty response)"}</Code>
                </div>
            ))}
        </Section>
    );
}
```

- [ ] **Step 4: ReportView**

`docker/plugins/wpsandbox/components/ReportView.tsx`:
```tsx
import { Link } from "react-router-dom";

import { Report } from "../api";
import { summarize } from "../report";
import { DatabaseSection } from "./sections/DatabaseSection";
import { FilesystemSection } from "./sections/FilesystemSection";
import { NetworkSection } from "./sections/NetworkSection";
import { PhpSection } from "./sections/PhpSection";
import { TriggerSection } from "./sections/TriggerSection";

function plural(n: number, one: string, many: string) {
    return `${n} ${n === 1 ? one : many}`;
}

export function ReportView({ report, blobId }: { report: Report; blobId: string }) {
    const s = summarize(report);
    const chips = [
        plural(s.filesCreated, "file created", "files created"),
        plural(s.filesModified, "file modified", "files modified"),
        plural(s.hosts.length, "host contacted", "hosts contacted"),
        plural(s.usersAdded.length, "user added", "users added"),
        plural(s.evalLayers, "eval layer", "eval layers"),
    ];
    return (
        <div>
            <div className="mb-2">
                {chips.map((c) => <span key={c} className="badge badge-pill badge-light border mr-1">{c}</span>)}
                <small className="text-muted ml-2">
                    WordPress {report.run.wp_version} · PHP {report.run.php_version} · {report.run.duration_s}s
                </small>
            </div>
            {s.hosts.length > 0 && (
                <p className="mb-2">Hosts: {s.hosts.map((h) => <code key={h} className="mr-2">{h}</code>)}</p>
            )}
            {s.truncated > 0 && (
                <div className="alert alert-warning py-1 px-2 small">
                    {plural(s.truncated, "field was truncated", "fields were truncated")} to fit the report:
                    <ul className="mb-0">{report.truncated.map((p) => <li key={p} className="text-monospace">{p}</li>)}</ul>
                </div>
            )}
            <NetworkSection report={report} />
            <FilesystemSection report={report} />
            <DatabaseSection report={report} />
            <PhpSection report={report} />
            <TriggerSection report={report} />
            <p className="mt-2"><Link to={`/blob/${blobId}`}>Raw JSON (blob)</Link></p>
        </div>
    );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox/__tests__/ReportView.test.tsx`
Expected: 6 passed. (If the "Database…3" badge assertion fails, recount against the fixture: 1 user added + 1 option modified + 1 other table = 3.)

- [ ] **Step 6: Commit**

```bash
git add docker/plugins/wpsandbox/components docker/plugins/wpsandbox/__tests__/ReportView.test.tsx
git commit -m "wpsandbox FE: report view with text-only sections"
```

---

### Task 5: Tab composition + plugin registration + manual verification

**Files:**
- Create: `docker/plugins/wpsandbox/components/WpSandboxTab.tsx`
- Create: `docker/plugins/wpsandbox/index.tsx`
- Modify: `docker/plugins/wpsandbox/README.md` (add UI section)

**Interfaces:**
- Consumes: `APIContext` (`api.axios`, `api.getObject`), `ObjectContext` (`object.id`, `object.file_name`), `useCheckCapabilities`, `Capability.addingBlobs`, `ObjectTab` — all as used in `docker/plugins/phpdeobf/`.

- [ ] **Step 1: Tab component**

`docker/plugins/wpsandbox/components/WpSandboxTab.tsx`:
```tsx
import { useCallback, useContext, useEffect, useState } from "react";

import { APIContext } from "@mwdb-web/commons/api";
import { useCheckCapabilities } from "@mwdb-web/commons/hooks/useCheckCapabilities";
import { ObjectContext } from "@mwdb-web/components/ShowObject";
import { Capability } from "@mwdb-web/types/types";
import type { ObjectData } from "@mwdb-web/types/types";

import { CreateRunBody, Report, Run, cancelRun, createRun, errorMessage, isActive, listRuns } from "../api";
import { usePolling } from "../hooks/usePolling";
import { parseReport } from "../report";
import { ReportView } from "./ReportView";
import { RunLauncher } from "./RunLauncher";
import { RunList } from "./RunList";

export function WpSandboxTab() {
    const api = useContext(APIContext);
    const objectContext = useContext(ObjectContext);
    const object = objectContext?.object as Partial<ObjectData> | undefined;
    const sha256 = object?.id ?? "";
    const { userHasCapabilities } = useCheckCapabilities();
    const canLaunch = userHasCapabilities(Capability.addingBlobs);

    const [runs, setRuns] = useState<Run[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    const [report, setReport] = useState<{ blobId: string; report: Report } | null>(null);
    const [reportError, setReportError] = useState<string | null>(null);

    const poll = useCallback(async () => {
        if (!sha256) return false;
        const list = await listRuns(api.axios, sha256);
        setRuns(list);
        setSelectedId((cur) => cur ?? list[0]?.id ?? null);
        return list.some(isActive);
    }, [api, sha256]);
    const { refresh } = usePolling(poll, 3000, [sha256]);

    const selected = runs.find((r) => r.id === selectedId) ?? null;

    useEffect(() => {
        const blobId = selected?.report_blob_id;
        if (!blobId) { setReport(null); setReportError(null); return; }
        if (report?.blobId === blobId) return;
        let cancelled = false;
        (async () => {
            try {
                const resp = await api.getObject("blob", blobId);
                const parsed = parseReport((resp.data as { content: string }).content);
                if (cancelled) return;
                if (parsed) { setReport({ blobId, report: parsed }); setReportError(null); }
                else setReportError("Report blob is not a valid v1 wp-sandbox report.");
            } catch (e) {
                if (!cancelled) setReportError(errorMessage(e, "Could not load report."));
            }
        })();
        return () => { cancelled = true; };
    }, [api, selected?.report_blob_id, report?.blobId]);

    async function onLaunch(body: CreateRunBody) {
        setBusy(true); setMessage(null);
        try {
            const res = await createRun(api.axios, sha256, body);
            setSelectedId(res.run_id);
            setMessage(res.existing ? "An identical run is already active — showing it." : null);
            refresh();
        } catch (e) {
            setMessage(errorMessage(e));
        } finally {
            setBusy(false);
        }
    }

    async function onCancel(id: string) {
        try { await cancelRun(api.axios, id); } catch (e) { setMessage(errorMessage(e)); }
        refresh();
    }

    return (
        <div style={{ padding: 20 }}>
            <h4>WordPress sandbox</h4>
            <RunLauncher sha256={sha256} fileName={object?.file_name} canLaunch={canLaunch} busy={busy} onLaunch={onLaunch} />
            {message && <div className="alert alert-info py-1 px-2">{message}</div>}
            <RunList runs={runs} selectedId={selectedId} onSelect={setSelectedId} onCancel={onCancel} />
            {selected && (selected.status === "failed" || selected.status === "timeout") && (
                <div className="alert alert-danger">
                    <strong>{selected.status}</strong>{selected.error && <pre className="mb-2" style={{ whiteSpace: "pre-wrap" }}>{selected.error}</pre>}
                    <button type="button" className="btn btn-sm btn-outline-danger" disabled={!canLaunch || busy}
                            onClick={() => onLaunch({ mode: selected.mode, ...(selected.params as Partial<CreateRunBody>) })}>
                        Re-run
                    </button>
                </div>
            )}
            {selected && isActive(selected) && <p className="text-muted"><em>Run {selected.status}… polling every 3 s.</em></p>}
            {reportError && <div className="alert alert-warning">{reportError}</div>}
            {report && selected?.report_blob_id === report.blobId && <ReportView report={report.report} blobId={report.blobId} />}
        </div>
    );
}
```

- [ ] **Step 2: Plugin registration**

`docker/plugins/wpsandbox/index.tsx`:
```tsx
import { faVial } from "@fortawesome/free-solid-svg-icons";

import { ObjectTab } from "@mwdb-web/components/ShowObject";

import { WpSandboxTab } from "./components/WpSandboxTab";

export default () => ({
    sampleTabsAfter: [
        () => <ObjectTab tab="wpsandbox" label="WP Sandbox" icon={faVial} component={WpSandboxTab} />,
    ],
});
```

- [ ] **Step 3: Format, type-check, full jest**

Run:
```bash
cd mwdb/web && npx prettier --write ../../docker/plugins/wpsandbox && npm run test
docker compose -f docker-compose-dev.yml up --build -d mwdb-web && docker compose -f docker-compose-dev.yml exec mwdb-web npx tsc
```
Expected: prettier rewrites nothing surprising, all jest suites pass, `tsc` clean.

- [ ] **Step 4: Manual verification in the dev stack (fake worker)**

With the stack and the fake worker from Stage 3 running: open a `.php` sample → "WP Sandbox" tab → Run. Expected: a `queued` row with spinner appears, turns `done` within ~10 s, the report renders with chips "2 files created · 1 file modified · 2 hosts contacted · 1 user added · 1 eval layer", the truncation notice lists `filesystem.created[1].content`, and "Raw JSON (blob)" opens the blob. Clicking Run again immediately shows the "identical run" info message and selects the existing run (or queues a new one if the first already finished). Log in as a user without `adding_blobs`: the button is disabled with the tooltip.

- [ ] **Step 5: README + commit**

Append to `docker/plugins/wpsandbox/README.md`:
```markdown
## UI

"WP Sandbox" tab on the sample page (`sampleTabsAfter`). Launcher → run list (polls every 3 s while
runs are active) → report view. All captured content is rendered as text.

Frontend tests: `cd mwdb/web && npx jest ../../docker/plugins/wpsandbox`
```

```bash
git add docker/plugins/wpsandbox
git commit -m "wpsandbox FE: WP Sandbox tab and plugin registration"
```

---

## Self-review

- **Spec coverage:** §5.4 launcher (mode, fields, default path, timeout, capability-disabled with tooltip, 409 handling) → Tasks 3, 5; run list with polling → Tasks 3, 5; report sections in the specified order with count badges, summary chips, truncation notice, failed → error + Re-run, raw JSON link → Tasks 4–5; text-only rendering (§8) → Task 4 test "captured html is rendered as text"; §9.5 jest tests → Tasks 1–4.
- **Placeholder scan:** none.
- **Type consistency:** `Run`, `Report`, `CreateRunBody`, `isActive`, `createRun → {run_id, existing}` from Task 1 used in Tasks 3–5; `summarize/parseReport/formatBytes` from Task 2 used in Task 4–5; `usePolling(fn, ms, deps) → {refresh}` from Task 3 used in Task 5.
