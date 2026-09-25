# Threat Library Plugin — Part 2: Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Threat library pages to the SPA as part of the `threatlib` plugin: a threat list, a one-form upload page with folder drop, a threat page with editable README and category, a "Threat library" nav entry, and a renderer that turns `jpop_threat_name` attribute values into links.

**Architecture:** All UI lives in `docker/plugins/threatlib/` next to the Python package (the same dual layout as `phpdeobf`). `index.tsx` default-exports `protectedRoutes`, `navdropdownExtras` and `attributeRenderers`; pages are React function components using the SPA's exported `View`, `ConfirmationModal`, `Autocomplete`, `DateString`, `Hash`, `ObjectLink`, `LoadingSpinner` components, `APIContext.axios` for the plugin API, and `AuthContext.hasCapability` for control visibility. Pure logic (path normalisation, markdown rendering) lives in small modules with jest tests; the pages are verified in the dev stack. Depends on Part 1 (backend API) being merged.

**Tech Stack:** React 18, TypeScript, react-router-dom 6, react-dropzone 14, axios, marked 15, dompurify 3, react-toastify, Bootstrap 4 classes (as used by the SPA), jest + ts-jest (web package).

**Spec:** `docs/superpowers/specs/2026-09-24-threatlib-sync-and-curation-design.md` (§6 Frontend)

## Global Constraints

- Package name `@mwdb-web/plugin-threatlib`, `main: ./index.tsx`, `private: true`. `Dockerfile-web` npm-installs every `docker/plugins/*/package.json`, so runtime deps declared there are installed; declare `dompurify` and `@types/dompurify` in the plugin's `package.json`.
- Imports from the SPA use the `@mwdb-web/*` alias exactly as `phpdeobf` does: `@mwdb-web/commons/api` (`APIContext`), `@mwdb-web/commons/auth` (`AuthContext`), `@mwdb-web/commons/ui` (components), `@mwdb-web/types/types` (`Capability`, `Attribute`, `AttributeDefinition`).
- Routes are added as children of the authenticated route group via `protectedRoutes`; paths are relative (`threatlib`, `threatlib/upload`, `threatlib/threat/:name`). Nav entry goes into the "Extras" dropdown via `navdropdownExtras` as a `<Link className="dropdown-item">`.
- Capability gating: controls that create/edit/link render only when `auth.hasCapability(Capability.addingFiles)`; delete only with `Capability.removingObjects`. Pages themselves are readable by any logged-in user.
- API paths (from Part 1): `GET /threatlib/threat?query=&category=&page=&per_page=`, `POST /threatlib/threat`, `GET|PUT|DELETE /threatlib/threat/<name>`, `POST /threatlib/threat/<name>/sample`, `DELETE /threatlib/threat/<name>/sample/<sha256>?rel_path=`, `POST /threatlib/upload` (multipart: `threat`, `category?`, `readme?`, `files` repeated, `rel_paths` repeated). `axios` in `APIContext` already has `baseURL` `/api`.
- Categories: `threats`, `for-later-review`, `webshells`, `escalated_issues_samples`.
- Formatting: `cd mwdb/web && npx prettier --config package.json --check "../../docker/plugins/threatlib/**/*.{ts,tsx}"` must pass (the SPA's prettier config lives in `mwdb/web/package.json`). Type errors surface in the dev web container log (`vite-plugin-checker`); the "verify in stack" steps read that log.
- Jest for pure modules: `cd mwdb/web && npx jest --roots ../../docker/plugins/threatlib --testMatch '**/*.test.ts' --modulePaths "$PWD/node_modules"`. Only dependency-free or npm-resolvable modules (`paths.ts`, `markdown.ts`) get jest tests; components are verified in the browser.
- Dev stack for verification: `./gen_vars.sh test && ./compose.sh --with dev --with plugins up --build -d`, app at `http://localhost:80`, admin login from `mwdb-vars.env`. `docker compose logs mwdb-web` shows type-checker output.

---

## File Structure

**New — under `docker/plugins/threatlib/`:**

| file | responsibility |
|---|---|
| `package.json` | npm package, deps `dompurify`, `@types/dompurify` |
| `tsconfig.json` | extends `../../../mwdb/web/tsconfig.json` (same as phpdeobf) |
| `index.tsx` | plugin hooks: `protectedRoutes`, `navdropdownExtras`, `attributeRenderers` |
| `api.ts` | typed axios calls and response types |
| `paths.ts` | `normalizeDroppedPath`, `stripTopFolder`, `clientValidateRelPath` |
| `paths.test.ts` | jest tests for `paths.ts` |
| `markdown.ts` | `renderMarkdown(md): string` (marked + DOMPurify) |
| `markdown.test.ts` | jest tests |
| `components/CategoryBadge.tsx` | small badge for a category |
| `components/ThreatListView.tsx` | `/threatlib` |
| `components/ThreatUploadView.tsx` | `/threatlib/upload` |
| `components/ThreatView.tsx` | `/threatlib/threat/:name` |
| `components/ThreatNameAttribute.tsx` | attribute renderer for `jpop_threat_name` |

**Modified:** `docker/plugins/threatlib/README.md` (add a "Frontend" section), `CLAUDE.md` (already mentions pages; no change needed unless wording differs).

---

### Task 1: npm package, API client and plugin hooks skeleton

**Files:**
- Create: `docker/plugins/threatlib/package.json`
- Create: `docker/plugins/threatlib/tsconfig.json`
- Create: `docker/plugins/threatlib/api.ts`
- Create: `docker/plugins/threatlib/components/CategoryBadge.tsx`
- Create: `docker/plugins/threatlib/index.tsx` (routes point at placeholder components replaced in later tasks)

**Interfaces:**
- Produces (`api.ts`): types `Category`, `ThreatSample {sha256: string | null; file_name: string | null; rel_path: string; added_at: string}`, `Threat {name; category: Category; readme: string | null; flat: boolean; created_by: string | null; created_at: string; updated_at: string; sample_count: number; samples?: ThreatSample[]}`, `ThreatList {threats: Threat[]; total: number; page: number; per_page: number}`, `UploadResult {rel_path: string; sha256: string | null; status: "new" | "existing" | "rejected"; reason?: string}`, `UploadResponse {threat: Threat; results: UploadResult[]}`; functions `listThreats(api, {query?, category?, page?, per_page?}, signal?)`, `getThreat(api, name, signal?)`, `createThreat(api, {name, category, readme})`, `updateThreat(api, name, {readme?, category?})`, `deleteThreat(api, name)`, `unlinkSample(api, name, sha256, rel_path)`, `uploadToThreat(api, form: UploadForm)` where `UploadForm = {threat: string; category?: Category; readme?: string; files: {file: File; rel_path: string}[]}`; constant `CATEGORIES: Category[]`; helper `errorMessage(e: unknown): string`.
- Produces (`index.tsx`): default export `() => ({ protectedRoutes: JSX.Element[], navdropdownExtras: JSX.Element[], attributeRenderers: Record<string, ComponentType> })`.

- [ ] **Step 1: Create package.json and tsconfig.json**

`docker/plugins/threatlib/package.json`:

```json
{
    "name": "@mwdb-web/plugin-threatlib",
    "version": "0.1.0",
    "description": "Threat library pages for MWDB (threats, upload, README)",
    "main": "./index.tsx",
    "private": true,
    "dependencies": {
        "dompurify": "^3.4.12"
    },
    "devDependencies": {
        "@types/dompurify": "^3.0.5"
    }
}
```

`docker/plugins/threatlib/tsconfig.json`:

```json
{
    "extends": "../../../mwdb/web/tsconfig.json",
    "include": ["**/*.ts", "**/*.tsx"],
    "exclude": ["node_modules"]
}
```

- [ ] **Step 2: Write api.ts**

`docker/plugins/threatlib/api.ts`:

```ts
import { AxiosInstance } from "axios";

export const CATEGORIES = [
    "threats",
    "for-later-review",
    "webshells",
    "escalated_issues_samples",
] as const;

export type Category = (typeof CATEGORIES)[number];

export type ThreatSample = {
    sha256: string | null;
    file_name: string | null;
    rel_path: string;
    added_at: string;
};

export type Threat = {
    name: string;
    category: Category;
    readme: string | null;
    flat: boolean;
    created_by: string | null;
    created_at: string;
    updated_at: string;
    sample_count: number;
    samples?: ThreatSample[];
};

export type ThreatList = {
    threats: Threat[];
    total: number;
    page: number;
    per_page: number;
};

export type UploadResult = {
    rel_path: string;
    sha256: string | null;
    status: "new" | "existing" | "rejected";
    reason?: string;
};

export type UploadResponse = {
    threat: Threat;
    results: UploadResult[];
};

export type UploadForm = {
    threat: string;
    category?: Category;
    readme?: string;
    files: { file: File; rel_path: string }[];
};

export async function listThreats(
    api: AxiosInstance,
    params: {
        query?: string;
        category?: Category | "";
        page?: number;
        per_page?: number;
    },
    signal?: AbortSignal,
): Promise<ThreatList> {
    const resp = await api.get<ThreatList>("/threatlib/threat", {
        params: {
            query: params.query || undefined,
            category: params.category || undefined,
            page: params.page,
            per_page: params.per_page,
        },
        signal,
    });
    return resp.data;
}

export async function getThreat(
    api: AxiosInstance,
    name: string,
    signal?: AbortSignal,
): Promise<Threat> {
    const resp = await api.get<Threat>(
        `/threatlib/threat/${encodeURIComponent(name)}`,
        { signal },
    );
    return resp.data;
}

export async function createThreat(
    api: AxiosInstance,
    body: { name: string; category: Category; readme?: string | null },
): Promise<Threat> {
    const resp = await api.post<Threat>("/threatlib/threat", body);
    return resp.data;
}

export async function updateThreat(
    api: AxiosInstance,
    name: string,
    body: { readme?: string | null; category?: Category },
): Promise<Threat> {
    const resp = await api.put<Threat>(
        `/threatlib/threat/${encodeURIComponent(name)}`,
        body,
    );
    return resp.data;
}

export async function deleteThreat(
    api: AxiosInstance,
    name: string,
): Promise<void> {
    await api.delete(`/threatlib/threat/${encodeURIComponent(name)}`);
}

export async function unlinkSample(
    api: AxiosInstance,
    name: string,
    sha256: string,
    rel_path: string,
): Promise<Threat> {
    const resp = await api.delete<Threat>(
        `/threatlib/threat/${encodeURIComponent(name)}/sample/${sha256}`,
        { params: { rel_path } },
    );
    return resp.data;
}

export async function uploadToThreat(
    api: AxiosInstance,
    form: UploadForm,
): Promise<UploadResponse> {
    const data = new FormData();
    data.append("threat", form.threat);
    if (form.category) data.append("category", form.category);
    if (form.readme) data.append("readme", form.readme);
    for (const { file, rel_path } of form.files) {
        data.append("files", file, file.name);
        data.append("rel_paths", rel_path);
    }
    const resp = await api.post<UploadResponse>("/threatlib/upload", data, {
        headers: { "Content-Type": "multipart/form-data" },
    });
    return resp.data;
}

export function errorMessage(e: unknown): string {
    const err = e as {
        response?: { status?: number; data?: { message?: string } };
        message?: string;
    };
    return (
        err.response?.data?.message ??
        err.message ??
        "Request failed."
    );
}
```

- [ ] **Step 3: Write CategoryBadge and the hooks skeleton**

`docker/plugins/threatlib/components/CategoryBadge.tsx`:

```tsx
import type { Category } from "../api";

const COLORS: Record<Category, string> = {
    threats: "badge-danger",
    "for-later-review": "badge-warning",
    webshells: "badge-dark",
    escalated_issues_samples: "badge-info",
};

export function CategoryBadge({ category }: { category: Category }) {
    return (
        <span className={`badge ${COLORS[category] ?? "badge-secondary"}`}>
            {category}
        </span>
    );
}
```

`docker/plugins/threatlib/index.tsx` (placeholders are replaced as each view lands):

```tsx
import { Link, Route } from "react-router-dom";

function Placeholder({ title }: { title: string }) {
    return <div className="container">{title}</div>;
}

export default () => ({
    protectedRoutes: [
        <Route
            key="threatlib-list"
            path="threatlib"
            element={<Placeholder title="Threat library" />}
        />,
        <Route
            key="threatlib-upload"
            path="threatlib/upload"
            element={<Placeholder title="Threat library upload" />}
        />,
        <Route
            key="threatlib-threat"
            path="threatlib/threat/:name"
            element={<Placeholder title="Threat" />}
        />,
    ],
    navdropdownExtras: [
        <Link key="threatlib" className="dropdown-item" to="/threatlib">
            Threat library
        </Link>,
    ],
    attributeRenderers: {},
});
```

- [ ] **Step 4: Verify in the dev stack**

```bash
./compose.sh --with dev --with plugins up --build -d mwdb-web
sleep 20
docker compose logs mwdb-web | grep -iE 'error|threatlib' | tail -20
```

Expected: no type errors mentioning `plugin-threatlib`. In the browser at `http://localhost:80`, log in, open the "Extras" dropdown: "Threat library" is listed and opens `/threatlib` showing the placeholder text.

- [ ] **Step 5: Format and commit**

```bash
cd mwdb/web && npx prettier --config package.json --write "../../docker/plugins/threatlib/**/*.{ts,tsx}" && cd ../..
git add docker/plugins/threatlib
git commit -m "threatlib: frontend package, API client and route/nav hooks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Pure helpers with jest tests (paths, markdown)

**Files:**
- Create: `docker/plugins/threatlib/paths.ts`
- Create: `docker/plugins/threatlib/paths.test.ts`
- Create: `docker/plugins/threatlib/markdown.ts`
- Create: `docker/plugins/threatlib/markdown.test.ts`

**Interfaces:**
- Produces (`paths.ts`): `normalizeDroppedPath(path: string | undefined, fallbackName: string): string` (strips leading `./` and `/`, collapses `//`, returns `fallbackName` when the path is empty); `stripTopFolder(rel: string): string` (removes the first segment when there are at least two); `clientValidateRelPath(rel: string): string | null` (returns an error message or `null`; mirrors backend rules: non-empty, no leading `/`, no `..` segment, no empty segment, no backslash, max 1024).
- Produces (`markdown.ts`): `renderMarkdown(md: string): string` returning sanitised HTML.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/paths.test.ts`:

```ts
import {
    clientValidateRelPath,
    normalizeDroppedPath,
    stripTopFolder,
} from "./paths";

describe("normalizeDroppedPath", () => {
    it("uses the file name when no path is given", () => {
        expect(normalizeDroppedPath(undefined, "a.php")).toBe("a.php");
        expect(normalizeDroppedPath("", "a.php")).toBe("a.php");
    });
    it("strips ./ and leading / from react-dropzone paths", () => {
        expect(normalizeDroppedPath("./a.php", "a.php")).toBe("a.php");
        expect(normalizeDroppedPath("/0154/wp-admin/menu.php", "menu.php")).toBe(
            "0154/wp-admin/menu.php",
        );
        expect(normalizeDroppedPath("./0154//x.php", "x.php")).toBe("0154/x.php");
    });
});

describe("stripTopFolder", () => {
    it("drops the first segment only when there are at least two", () => {
        expect(stripTopFolder("0154/wp-admin/menu.php")).toBe("wp-admin/menu.php");
        expect(stripTopFolder("a.php")).toBe("a.php");
    });
});

describe("clientValidateRelPath", () => {
    it("accepts good paths", () => {
        expect(clientValidateRelPath("a.php")).toBeNull();
        expect(clientValidateRelPath(".hidden")).toBeNull();
        expect(clientValidateRelPath("0154/wp-admin/menu.php")).toBeNull();
    });
    it("rejects bad paths with a message", () => {
        expect(clientValidateRelPath("")).toMatch(/required/);
        expect(clientValidateRelPath("/abs.php")).toMatch(/relative/);
        expect(clientValidateRelPath("../x.php")).toMatch(/\.\./);
        expect(clientValidateRelPath("a//b.php")).toMatch(/empty/);
        expect(clientValidateRelPath("a\\b.php")).toMatch(/separator/);
        expect(clientValidateRelPath("x".repeat(1025))).toMatch(/long/);
    });
});
```

`docker/plugins/threatlib/markdown.test.ts`:

```ts
import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
    it("renders headings and links", () => {
        const html = renderMarkdown("# Title\n\n[x](https://example.com)");
        expect(html).toContain("<h1");
        expect(html).toContain('href="https://example.com"');
    });
    it("strips scripts and event handlers", () => {
        const html = renderMarkdown('<img src=x onerror="alert(1)"><script>alert(1)</script>');
        expect(html).not.toContain("onerror");
        expect(html).not.toContain("<script");
    });
    it("returns empty string for empty input", () => {
        expect(renderMarkdown("")).toBe("");
    });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd mwdb/web && npx jest --roots ../../docker/plugins/threatlib --testMatch '**/*.test.ts' --modulePaths "$PWD/node_modules"
```

Expected: FAIL with `Cannot find module './paths'` / `'./markdown'`.

- [ ] **Step 3: Implement the helpers**

`docker/plugins/threatlib/paths.ts`:

```ts
const MAX_REL_PATH_LEN = 1024;

/** react-dropzone (file-selector) puts the drop-relative path on `file.path`:
 *  "./a.php" for plain files, "/folder/sub/x.php" for directory drops. */
export function normalizeDroppedPath(
    path: string | undefined,
    fallbackName: string,
): string {
    let p = (path ?? "").replace(/\\/g, "/");
    p = p.replace(/^(\.\/)+/, "").replace(/^\/+/, "");
    p = p.replace(/\/{2,}/g, "/");
    return p || fallbackName;
}

export function stripTopFolder(rel: string): string {
    const idx = rel.indexOf("/");
    return idx === -1 ? rel : rel.slice(idx + 1);
}

export function clientValidateRelPath(rel: string): string | null {
    if (!rel) return "rel_path is required";
    if (rel.length > MAX_REL_PATH_LEN) return "rel_path is too long";
    if (rel.includes("\\")) return "rel_path must use '/' as separator";
    if (rel.startsWith("/")) return "rel_path must be relative";
    if (rel.endsWith("/")) return "rel_path must name a file";
    if (rel.includes("//")) return "rel_path contains an empty segment";
    if (rel.split("/").some((s) => s === "..")) return "rel_path cannot contain '..'";
    return null;
}
```

`docker/plugins/threatlib/markdown.ts`:

```ts
import DOMPurify from "dompurify";
import { marked } from "marked";

export function renderMarkdown(md: string): string {
    if (!md) return "";
    const html = marked.parse(md, { async: false }) as string;
    return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd mwdb/web && npx jest --roots ../../docker/plugins/threatlib --testMatch '**/*.test.ts' --modulePaths "$PWD/node_modules"
```

Expected: 2 suites, all tests passed. If `dompurify` cannot be resolved locally, run `cd mwdb/web && npm ls dompurify` to confirm it is present as a transitive dependency; if not, `npm install --no-save dompurify` for the local run only (Docker installs it from the plugin's `package.json`).

- [ ] **Step 5: Format and commit**

```bash
cd mwdb/web && npx prettier --config package.json --write "../../docker/plugins/threatlib/**/*.{ts,tsx}" && cd ../..
git add docker/plugins/threatlib
git commit -m "threatlib: path normalisation and markdown helpers with tests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Threat list page

**Files:**
- Create: `docker/plugins/threatlib/components/ThreatListView.tsx`
- Modify: `docker/plugins/threatlib/index.tsx` (route element)

**Interfaces:**
- Consumes: `api.listThreats`, `CategoryBadge`, `View`, `DateString`, `LoadingSpinner` from `@mwdb-web/commons/ui`, `APIContext`, `AuthContext`, `Capability`.
- Produces: `ThreatListView` component. Query state lives in the URL search params (`query`, `category`, `page`) so links are shareable.

- [ ] **Step 1: Write the view**

`docker/plugins/threatlib/components/ThreatListView.tsx`:

```tsx
import { useContext, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { APIContext } from "@mwdb-web/commons/api";
import { AuthContext } from "@mwdb-web/commons/auth";
import { DateString, LoadingSpinner, View } from "@mwdb-web/commons/ui";
import { Capability } from "@mwdb-web/types/types";

import { CATEGORIES, Category, ThreatList, errorMessage, listThreats } from "../api";
import { CategoryBadge } from "./CategoryBadge";

const PER_PAGE = 50;

export function ThreatListView() {
    const api = useContext(APIContext);
    const auth = useContext(AuthContext);
    const navigate = useNavigate();
    const [params, setParams] = useSearchParams();
    const query = params.get("query") ?? "";
    const category = (params.get("category") ?? "") as Category | "";
    const page = Math.max(1, parseInt(params.get("page") ?? "1", 10) || 1);

    const [data, setData] = useState<ThreatList | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(query);

    useEffect(() => {
        const ctrl = new AbortController();
        setError(null);
        listThreats(
            api.axios,
            { query, category, page, per_page: PER_PAGE },
            ctrl.signal,
        )
            .then(setData)
            .catch((e) => {
                if (!ctrl.signal.aborted) setError(errorMessage(e));
            });
        return () => ctrl.abort();
    }, [api, query, category, page]);

    function update(next: { query?: string; category?: string; page?: number }) {
        const p = new URLSearchParams(params);
        if (next.query !== undefined) {
            next.query ? p.set("query", next.query) : p.delete("query");
            p.delete("page");
        }
        if (next.category !== undefined) {
            next.category ? p.set("category", next.category) : p.delete("category");
            p.delete("page");
        }
        if (next.page !== undefined) {
            next.page > 1 ? p.set("page", String(next.page)) : p.delete("page");
        }
        setParams(p);
    }

    const pages = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1;

    return (
        <View ident="threatlibList" fluid>
            <div className="d-flex align-items-center mb-3">
                <h4 className="mb-0">Threat library</h4>
                {auth.hasCapability(Capability.addingFiles) && (
                    <Link className="btn btn-primary btn-sm ml-auto" to="/threatlib/upload">
                        Add samples
                    </Link>
                )}
            </div>
            <form
                className="form-inline mb-3"
                onSubmit={(ev) => {
                    ev.preventDefault();
                    update({ query: pending });
                }}
            >
                <input
                    className="form-control mr-2"
                    placeholder="Name starts with…"
                    value={pending}
                    onChange={(ev) => setPending(ev.target.value)}
                />
                <select
                    className="form-control mr-2"
                    value={category}
                    onChange={(ev) => update({ category: ev.target.value })}
                >
                    <option value="">All categories</option>
                    {CATEGORIES.map((c) => (
                        <option key={c} value={c}>
                            {c}
                        </option>
                    ))}
                </select>
                <button className="btn btn-outline-secondary" type="submit">
                    Search
                </button>
            </form>
            {error && <div className="alert alert-danger">{error}</div>}
            {!data && !error && <LoadingSpinner loading />}
            {data && (
                <>
                    <table className="table table-striped table-bordered table-hover">
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>Category</th>
                                <th>Samples</th>
                                <th>Updated</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.threats.length === 0 && (
                                <tr>
                                    <td colSpan={4} className="text-muted">
                                        No threats match.
                                    </td>
                                </tr>
                            )}
                            {data.threats.map((t) => (
                                <tr
                                    key={t.name}
                                    style={{ cursor: "pointer" }}
                                    onClick={() =>
                                        navigate(`/threatlib/threat/${encodeURIComponent(t.name)}`)
                                    }
                                >
                                    <td>
                                        <Link to={`/threatlib/threat/${encodeURIComponent(t.name)}`}>
                                            {t.name}
                                        </Link>
                                    </td>
                                    <td>
                                        <CategoryBadge category={t.category} />
                                    </td>
                                    <td>{t.sample_count}</td>
                                    <td>
                                        <DateString date={t.updated_at} />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <div className="d-flex align-items-center">
                        <span className="text-muted">
                            {data.total} threats, page {data.page} of {pages}
                        </span>
                        <div className="ml-auto">
                            <button
                                className="btn btn-sm btn-outline-secondary mr-2"
                                disabled={page <= 1}
                                onClick={() => update({ page: page - 1 })}
                            >
                                Previous
                            </button>
                            <button
                                className="btn btn-sm btn-outline-secondary"
                                disabled={page >= pages}
                                onClick={() => update({ page: page + 1 })}
                            >
                                Next
                            </button>
                        </div>
                    </div>
                </>
            )}
        </View>
    );
}
```

Check `LoadingSpinner`'s props in `mwdb/web/src/commons/ui/LoadingSpinner.tsx` before use; if it takes no `loading` prop, render it bare.

- [ ] **Step 2: Wire the route**

In `index.tsx`, replace the list placeholder with `<ThreatListView />` (import from `./components/ThreatListView`).

- [ ] **Step 3: Verify in the dev stack**

With Part 1 running, create two threats through the API and load `/threatlib`:

```bash
TOKEN=$(curl -s -X POST http://localhost/api/auth/login -H 'Content-Type: application/json' -d "{\"login\":\"admin\",\"password\":\"$(grep MWDB_ADMIN_PASSWORD mwdb-vars.env | cut -d= -f2)\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -X POST http://localhost/api/threatlib/threat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"FIO-1","category":"threats","readme":"# FIO-1"}'
curl -s -X POST http://localhost/api/threatlib/threat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"wf-9","category":"for-later-review"}'
docker compose logs mwdb-web | grep -iE 'error' | tail -5
```

Expected in the browser: both rows listed with badges; typing `FIO` and Search filters to one; category filter works; "Add samples" button visible for admin; row click opens the (placeholder) threat page. No type errors in the log.

- [ ] **Step 4: Format and commit**

```bash
cd mwdb/web && npx prettier --config package.json --write "../../docker/plugins/threatlib/**/*.{ts,tsx}" && cd ../..
git add docker/plugins/threatlib
git commit -m "threatlib: threat list page

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Upload page with folder drop

**Files:**
- Create: `docker/plugins/threatlib/components/ThreatUploadView.tsx`
- Modify: `docker/plugins/threatlib/index.tsx` (route element, wrapped in `RequiresCapability`)

**Interfaces:**
- Consumes: `api.listThreats` (autocomplete), `api.getThreat`, `api.uploadToThreat`, `paths.*`, `Autocomplete`, `View` from `@mwdb-web/commons/ui`, `useDropzone` from `react-dropzone`, `toast` from `react-toastify`.
- Produces: `ThreatUploadView`. Behaviour: threat name input with autocomplete (debounced `listThreats({query})`); when the typed name matches an existing threat exactly (looked up via `getThreat`, 404 means new), category and README fields are hidden and a link to the threat page is shown; otherwise category select and README textarea (pre-filled `# <name>\n`) appear. Drop zone accepts files and folders; each dropped file becomes a row `{file, rel_path}` with an editable `rel_path` pre-filled by `normalizeDroppedPath`, a "Strip top folder" button applies `stripTopFolder` to every row, and rows can be removed. Submit is disabled until the name is set, at least one file is present, every `rel_path` passes `clientValidateRelPath`, and for a new threat a category is chosen. On success a results table lists each file's status and a link to the threat page.

- [ ] **Step 1: Write the view**

`docker/plugins/threatlib/components/ThreatUploadView.tsx`:

```tsx
import { useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "react-toastify";

import { APIContext } from "@mwdb-web/commons/api";
import { Autocomplete, View } from "@mwdb-web/commons/ui";

import {
    CATEGORIES,
    Category,
    Threat,
    UploadResponse,
    errorMessage,
    getThreat,
    listThreats,
    uploadToThreat,
} from "../api";
import { clientValidateRelPath, normalizeDroppedPath, stripTopFolder } from "../paths";
import { CategoryBadge } from "./CategoryBadge";

type Row = { id: number; file: File; rel_path: string };

type DroppedFile = File & { path?: string };

export function ThreatUploadView() {
    const api = useContext(APIContext);
    const [params] = useSearchParams();

    const [name, setName] = useState(params.get("threat") ?? "");
    const [suggestions, setSuggestions] = useState<Threat[]>([]);
    const [existing, setExisting] = useState<Threat | null>(null);
    const [category, setCategory] = useState<Category | "">("");
    const [readme, setReadme] = useState("");
    const [readmeTouched, setReadmeTouched] = useState(false);
    const [rows, setRows] = useState<Row[]>([]);
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState<UploadResponse | null>(null);

    // Autocomplete suggestions (debounced).
    useEffect(() => {
        if (!name) {
            setSuggestions([]);
            return;
        }
        const ctrl = new AbortController();
        const t = setTimeout(() => {
            listThreats(api.axios, { query: name, per_page: 10 }, ctrl.signal)
                .then((r) => setSuggestions(r.threats))
                .catch(() => {});
        }, 200);
        return () => {
            clearTimeout(t);
            ctrl.abort();
        };
    }, [api, name]);

    // Exact-match lookup decides "existing" vs "new".
    useEffect(() => {
        if (!name) {
            setExisting(null);
            return;
        }
        const ctrl = new AbortController();
        getThreat(api.axios, name, ctrl.signal)
            .then((t) => setExisting(t))
            .catch(() => {
                if (!ctrl.signal.aborted) setExisting(null);
            });
        return () => ctrl.abort();
    }, [api, name]);

    useEffect(() => {
        if (!readmeTouched) setReadme(name ? `# ${name}\n` : "");
    }, [name, readmeTouched]);

    const onDrop = useCallback((accepted: DroppedFile[]) => {
        setRows((prev) => {
            let nextId = prev.length ? Math.max(...prev.map((r) => r.id)) + 1 : 1;
            const added = accepted.map((file) => ({
                id: nextId++,
                file,
                rel_path: normalizeDroppedPath(file.path, file.name),
            }));
            return [...prev, ...added];
        });
    }, []);

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop,
        multiple: true,
        useFsAccessApi: false,
    });

    const rowErrors = useMemo(
        () => rows.map((r) => clientValidateRelPath(r.rel_path)),
        [rows],
    );
    const duplicatePaths = useMemo(() => {
        const seen = new Set<string>();
        const dups = new Set<string>();
        for (const r of rows) {
            if (seen.has(r.rel_path)) dups.add(r.rel_path);
            seen.add(r.rel_path);
        }
        return dups;
    }, [rows]);

    const isNew = !!name && existing === null;
    const canSubmit =
        !busy &&
        !!name &&
        rows.length > 0 &&
        rowErrors.every((e) => e === null) &&
        duplicatePaths.size === 0 &&
        (!isNew || !!category);

    async function submit(ev: React.FormEvent) {
        ev.preventDefault();
        if (!canSubmit) return;
        setBusy(true);
        try {
            const resp = await uploadToThreat(api.axios, {
                threat: name,
                category: isNew ? (category as Category) : undefined,
                readme: isNew ? readme : undefined,
                files: rows.map((r) => ({ file: r.file, rel_path: r.rel_path })),
            });
            setResult(resp);
            setRows([]);
            toast(`Uploaded ${resp.results.length} file(s) to ${resp.threat.name}`, {
                type: "success",
            });
        } catch (e) {
            toast(errorMessage(e), { type: "error" });
        } finally {
            setBusy(false);
        }
    }

    const threatLink = (n: string) => `/threatlib/threat/${encodeURIComponent(n)}`;

    return (
        <View ident="threatlibUpload">
            <h4>Add samples to the threat library</h4>
            <form onSubmit={submit}>
                <div className="form-group">
                    <label>Threat</label>
                    <Autocomplete
                        items={suggestions}
                        getItemValue={(t) => t.name}
                        renderItem={({ item }) => (
                            <span>
                                {item.name} <CategoryBadge category={item.category} />
                            </span>
                        )}
                        value={name}
                        onChange={(v) => setName(v.trim())}
                        placeholder="Existing threat name, or a new one"
                        className="form-control"
                    />
                    {existing && (
                        <small className="form-text text-muted">
                            Existing threat <CategoryBadge category={existing.category} /> with{" "}
                            {existing.sample_count} sample(s).{" "}
                            <Link to={threatLink(existing.name)}>Edit its README there.</Link>
                        </small>
                    )}
                    {isNew && (
                        <small className="form-text text-muted">
                            New threat will be created.
                        </small>
                    )}
                </div>
                {isNew && (
                    <>
                        <div className="form-group">
                            <label>Category</label>
                            <select
                                className="form-control"
                                value={category}
                                onChange={(ev) => setCategory(ev.target.value as Category)}
                            >
                                <option value="">Choose…</option>
                                {CATEGORIES.map((c) => (
                                    <option key={c} value={c}>
                                        {c}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div className="form-group">
                            <label>README (markdown)</label>
                            <textarea
                                className="form-control text-monospace"
                                rows={6}
                                value={readme}
                                onChange={(ev) => {
                                    setReadmeTouched(true);
                                    setReadme(ev.target.value);
                                }}
                            />
                        </div>
                    </>
                )}
                <div
                    {...getRootProps({
                        className: `dropzone-ready dropzone ${isDragActive ? "dropzone-active" : ""}`,
                    })}
                >
                    <input {...getInputProps()} />
                    <div className="card">
                        <div className="card-body">
                            Drop files or folders here, or click to choose files.
                            Folder structure is kept as the path inside the threat.
                        </div>
                    </div>
                </div>
                {rows.length > 0 && (
                    <>
                        <div className="d-flex align-items-center mt-3 mb-1">
                            <strong>{rows.length} file(s)</strong>
                            <button
                                type="button"
                                className="btn btn-sm btn-outline-secondary ml-auto"
                                onClick={() =>
                                    setRows((prev) =>
                                        prev.map((r) => ({ ...r, rel_path: stripTopFolder(r.rel_path) })),
                                    )
                                }
                            >
                                Strip top folder
                            </button>
                        </div>
                        <table className="table table-sm table-bordered">
                            <thead>
                                <tr>
                                    <th>Path inside threat</th>
                                    <th>Size</th>
                                    <th />
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((r, i) => (
                                    <tr key={r.id}>
                                        <td>
                                            <input
                                                className={`form-control form-control-sm text-monospace ${
                                                    rowErrors[i] || duplicatePaths.has(r.rel_path)
                                                        ? "is-invalid"
                                                        : ""
                                                }`}
                                                value={r.rel_path}
                                                onChange={(ev) =>
                                                    setRows((prev) =>
                                                        prev.map((x) =>
                                                            x.id === r.id
                                                                ? { ...x, rel_path: ev.target.value }
                                                                : x,
                                                        ),
                                                    )
                                                }
                                            />
                                            {(rowErrors[i] || duplicatePaths.has(r.rel_path)) && (
                                                <small className="text-danger">
                                                    {rowErrors[i] ?? "duplicate path"}
                                                </small>
                                            )}
                                        </td>
                                        <td>{r.file.size} B</td>
                                        <td>
                                            <button
                                                type="button"
                                                className="btn btn-sm btn-link text-danger"
                                                onClick={() =>
                                                    setRows((prev) => prev.filter((x) => x.id !== r.id))
                                                }
                                            >
                                                remove
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </>
                )}
                <button className="btn btn-primary mt-3" type="submit" disabled={!canSubmit}>
                    {busy ? "Uploading…" : "Upload"}
                </button>
            </form>
            {result && (
                <div className="mt-4">
                    <h5>
                        Uploaded to <Link to={threatLink(result.threat.name)}>{result.threat.name}</Link>{" "}
                        <CategoryBadge category={result.threat.category} />
                    </h5>
                    <table className="table table-sm table-bordered">
                        <thead>
                            <tr>
                                <th>Path</th>
                                <th>Status</th>
                                <th>SHA256</th>
                            </tr>
                        </thead>
                        <tbody>
                            {result.results.map((r) => (
                                <tr key={r.rel_path}>
                                    <td className="text-monospace">{r.rel_path}</td>
                                    <td>
                                        <span
                                            className={`badge ${
                                                r.status === "new"
                                                    ? "badge-success"
                                                    : r.status === "existing"
                                                    ? "badge-secondary"
                                                    : "badge-danger"
                                            }`}
                                        >
                                            {r.status}
                                        </span>{" "}
                                        {r.reason}
                                    </td>
                                    <td className="text-monospace">
                                        {r.sha256 ? (
                                            <Link to={`/file/${r.sha256}`}>{r.sha256.slice(0, 16)}…</Link>
                                        ) : (
                                            "-"
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </View>
    );
}
```

- [ ] **Step 2: Wire the route with capability gating**

In `index.tsx`:

```tsx
import { RequiresCapability } from "@mwdb-web/commons/ui";
import { Capability } from "@mwdb-web/types/types";
// ...
<Route
    key="threatlib-upload"
    path="threatlib/upload"
    element={
        <RequiresCapability capability={Capability.addingFiles}>
            <ThreatUploadView />
        </RequiresCapability>
    }
/>
```

- [ ] **Step 3: Verify in the dev stack**

In the browser at `/threatlib/upload`:
1. Type `FIO-1` (exists from Task 3): the category/README fields hide and the "Existing threat" hint shows.
2. Type `NEW-9`: category select and README with `# NEW-9` appear.
3. Drop a folder containing `sub/x.php` and `y.php`: two rows with paths `<folder>/sub/x.php` and `<folder>/y.php`; click "Strip top folder": paths become `sub/x.php` and `y.php`; edit one to `../bad` and see the inline error and the disabled Upload button.
4. Pick category `for-later-review`, upload: results table shows two `new` rows with links; the threat page link works. Upload the same files again to `NEW-9`: statuses are `existing`.
5. `docker compose logs mwdb-web | grep -iE 'error' | tail` shows nothing new.

- [ ] **Step 4: Format and commit**

```bash
cd mwdb/web && npx prettier --config package.json --write "../../docker/plugins/threatlib/**/*.{ts,tsx}" && cd ../..
git add docker/plugins/threatlib
git commit -m "threatlib: upload page with folder drop and inline threat creation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Threat page

**Files:**
- Create: `docker/plugins/threatlib/components/ThreatView.tsx`
- Modify: `docker/plugins/threatlib/index.tsx` (route element)

**Interfaces:**
- Consumes: `api.getThreat`, `api.updateThreat`, `api.deleteThreat`, `api.unlinkSample`, `markdown.renderMarkdown`, `ConfirmationModal`, `DateString`, `Hash`, `View`, `LoadingSpinner` from `@mwdb-web/commons/ui`, `AuthContext`, `Capability`, `toast`, `useParams`, `useNavigate`.
- Produces: `ThreatView`. Left column: name, `CategoryBadge` plus a category `<select>` (only with `addingFiles`), created by/at, updated at, README rendered via `renderMarkdown` inside `dangerouslySetInnerHTML`, "Edit" toggles a textarea with Save/Cancel. Right column: sample table with `rel_path`, file name, sha256 (link to `/file/<sha256>`; `-` for empty files), added date, unlink icon (with `addingFiles`) opening a `ConfirmationModal`. Bottom: "Delete threat" (with `removingObjects`) opening a `ConfirmationModal`; on confirm navigate to `/threatlib`.

- [ ] **Step 1: Write the view**

`docker/plugins/threatlib/components/ThreatView.tsx`:

```tsx
import { useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "react-toastify";

import { APIContext } from "@mwdb-web/commons/api";
import { AuthContext } from "@mwdb-web/commons/auth";
import { ConfirmationModal, DateString, LoadingSpinner, View } from "@mwdb-web/commons/ui";
import { Capability } from "@mwdb-web/types/types";

import {
    CATEGORIES,
    Category,
    Threat,
    ThreatSample,
    deleteThreat,
    errorMessage,
    getThreat,
    unlinkSample,
    updateThreat,
} from "../api";
import { renderMarkdown } from "../markdown";
import { CategoryBadge } from "./CategoryBadge";

export function ThreatView() {
    const api = useContext(APIContext);
    const auth = useContext(AuthContext);
    const navigate = useNavigate();
    const { name = "" } = useParams<{ name: string }>();

    const [threat, setThreat] = useState<Threat | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState("");
    const [unlinkTarget, setUnlinkTarget] = useState<ThreatSample | null>(null);
    const [confirmDelete, setConfirmDelete] = useState(false);

    const canEdit = auth.hasCapability(Capability.addingFiles);
    const canDelete = auth.hasCapability(Capability.removingObjects);

    const load = useCallback(
        (signal?: AbortSignal) =>
            getThreat(api.axios, name, signal)
                .then((t) => {
                    setThreat(t);
                    setError(null);
                })
                .catch((e) => {
                    if (!signal?.aborted) setError(errorMessage(e));
                }),
        [api, name],
    );

    useEffect(() => {
        const ctrl = new AbortController();
        load(ctrl.signal);
        return () => ctrl.abort();
    }, [load]);

    const readmeHtml = useMemo(
        () => renderMarkdown(threat?.readme ?? ""),
        [threat?.readme],
    );

    async function saveReadme() {
        try {
            setThreat(await updateThreat(api.axios, name, { readme: draft || null }));
            setEditing(false);
            toast("README saved", { type: "success" });
        } catch (e) {
            toast(errorMessage(e), { type: "error" });
        }
    }

    async function changeCategory(category: Category) {
        try {
            setThreat(await updateThreat(api.axios, name, { category }));
            toast(`Category set to ${category}`, { type: "success" });
        } catch (e) {
            toast(errorMessage(e), { type: "error" });
        }
    }

    async function doUnlink() {
        if (!unlinkTarget?.sha256) return;
        try {
            setThreat(await unlinkSample(api.axios, name, unlinkTarget.sha256, unlinkTarget.rel_path));
            toast(`Unlinked ${unlinkTarget.rel_path}`, { type: "success" });
        } catch (e) {
            toast(errorMessage(e), { type: "error" });
        } finally {
            setUnlinkTarget(null);
        }
    }

    async function doDelete() {
        try {
            await deleteThreat(api.axios, name);
            toast(`Deleted threat ${name}`, { type: "success" });
            navigate("/threatlib");
        } catch (e) {
            toast(errorMessage(e), { type: "error" });
            setConfirmDelete(false);
        }
    }

    if (error) {
        return (
            <View ident="threatlibThreat">
                <div className="alert alert-danger">{error}</div>
                <Link to="/threatlib">Back to the threat library</Link>
            </View>
        );
    }
    if (!threat) {
        return (
            <View ident="threatlibThreat">
                <LoadingSpinner loading />
            </View>
        );
    }

    return (
        <View ident="threatlibThreat" fluid>
            <div className="row">
                <div className="col-md-5">
                    <div className="card mb-3">
                        <div className="card-header d-flex align-items-center">
                            <h5 className="mb-0 text-monospace">{threat.name}</h5>
                            <span className="ml-2">
                                <CategoryBadge category={threat.category} />
                            </span>
                            {canEdit && (
                                <select
                                    className="form-control form-control-sm ml-auto"
                                    style={{ width: "auto" }}
                                    value={threat.category}
                                    onChange={(ev) => changeCategory(ev.target.value as Category)}
                                >
                                    {CATEGORIES.map((c) => (
                                        <option key={c} value={c}>
                                            {c}
                                        </option>
                                    ))}
                                </select>
                            )}
                        </div>
                        <div className="card-body">
                            <table className="table table-sm mb-3">
                                <tbody>
                                    <tr>
                                        <th>Created by</th>
                                        <td>{threat.created_by ?? "-"}</td>
                                    </tr>
                                    <tr>
                                        <th>Created</th>
                                        <td>
                                            <DateString date={threat.created_at} />
                                        </td>
                                    </tr>
                                    <tr>
                                        <th>Updated</th>
                                        <td>
                                            <DateString date={threat.updated_at} />
                                        </td>
                                    </tr>
                                    <tr>
                                        <th>Samples</th>
                                        <td>{threat.sample_count}</td>
                                    </tr>
                                </tbody>
                            </table>
                            <div className="d-flex align-items-center mb-2">
                                <strong>README</strong>
                                {canEdit && !editing && (
                                    <button
                                        className="btn btn-sm btn-outline-secondary ml-auto"
                                        onClick={() => {
                                            setDraft(threat.readme ?? "");
                                            setEditing(true);
                                        }}
                                    >
                                        Edit
                                    </button>
                                )}
                            </div>
                            {editing ? (
                                <>
                                    <textarea
                                        className="form-control text-monospace"
                                        rows={14}
                                        value={draft}
                                        onChange={(ev) => setDraft(ev.target.value)}
                                    />
                                    <div className="mt-2">
                                        <button className="btn btn-sm btn-primary mr-2" onClick={saveReadme}>
                                            Save
                                        </button>
                                        <button
                                            className="btn btn-sm btn-outline-secondary"
                                            onClick={() => setEditing(false)}
                                        >
                                            Cancel
                                        </button>
                                    </div>
                                </>
                            ) : threat.readme ? (
                                <div
                                    className="threatlib-readme"
                                    dangerouslySetInnerHTML={{ __html: readmeHtml }}
                                />
                            ) : (
                                <span className="text-muted">No README.</span>
                            )}
                        </div>
                    </div>
                    {canDelete && (
                        <button className="btn btn-outline-danger btn-sm" onClick={() => setConfirmDelete(true)}>
                            Delete threat
                        </button>
                    )}
                </div>
                <div className="col-md-7">
                    <div className="d-flex align-items-center mb-2">
                        <h5 className="mb-0">Samples</h5>
                        {canEdit && (
                            <Link
                                className="btn btn-sm btn-primary ml-auto"
                                to={`/threatlib/upload?threat=${encodeURIComponent(threat.name)}`}
                            >
                                Add samples
                            </Link>
                        )}
                    </div>
                    <table className="table table-sm table-striped table-bordered">
                        <thead>
                            <tr>
                                <th>Path</th>
                                <th>File name</th>
                                <th>SHA256</th>
                                <th>Added</th>
                                {canEdit && <th />}
                            </tr>
                        </thead>
                        <tbody>
                            {(threat.samples ?? []).map((s) => (
                                <tr key={s.rel_path}>
                                    <td className="text-monospace">{s.rel_path}</td>
                                    <td>{s.file_name ?? <span className="text-muted">(empty file)</span>}</td>
                                    <td className="text-monospace">
                                        {s.sha256 ? (
                                            <Link to={`/file/${s.sha256}`}>{s.sha256.slice(0, 16)}…</Link>
                                        ) : (
                                            "-"
                                        )}
                                    </td>
                                    <td>
                                        <DateString date={s.added_at} />
                                    </td>
                                    {canEdit && (
                                        <td>
                                            {s.sha256 && (
                                                <button
                                                    className="btn btn-sm btn-link text-danger"
                                                    title="Unlink from this threat"
                                                    onClick={() => setUnlinkTarget(s)}
                                                >
                                                    unlink
                                                </button>
                                            )}
                                        </td>
                                    )}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
            <ConfirmationModal
                isOpen={unlinkTarget !== null}
                message={`Unlink ${unlinkTarget?.rel_path} from ${threat.name}? The sample itself is kept.`}
                onRequestClose={() => setUnlinkTarget(null)}
                onCancel={() => setUnlinkTarget(null)}
                onConfirm={doUnlink}
                confirmText="Unlink"
                buttonStyle="btn-danger"
            />
            <ConfirmationModal
                isOpen={confirmDelete}
                message={`Delete threat ${threat.name}? Its ${threat.sample_count} sample(s) stay in MWDB but are unlinked.`}
                onRequestClose={() => setConfirmDelete(false)}
                onCancel={() => setConfirmDelete(false)}
                onConfirm={doDelete}
                confirmText="Delete"
                buttonStyle="btn-danger"
            />
        </View>
    );
}
```

Check `ConfirmationModal`'s rendered button props in `mwdb/web/src/commons/ui/ConfirmationModal.tsx` (lines 30 onward) and adjust `buttonStyle`/`confirmText` names if they differ from the `Props` type shown at the top of that file.

- [ ] **Step 2: Wire the route**

In `index.tsx`, replace the threat placeholder with `<ThreatView />`.

- [ ] **Step 3: Verify in the dev stack**

Open `/threatlib/threat/NEW-9` (created in Task 4):
1. README renders as HTML; Edit → change text → Save updates the rendered README and the "Updated" time.
2. Change category to `threats` via the select: badge updates; the sample page of one of its files (`/file/<sha256>`) now shows tag `threats` and not `for-later-review`.
3. Unlink one sample: confirmation modal, then the row disappears and the sample count decrements; the file still exists at `/file/<sha256>` without the `jpop_threat_name` attribute.
4. "Add samples" pre-fills the upload form with the threat name.
5. Delete threat: confirmation, redirect to `/threatlib`, threat gone from the list.
6. Log in as a user without `adding_files` (create one under Settings → Users): no Edit/select/unlink/Delete controls, page still readable.

- [ ] **Step 4: Format and commit**

```bash
cd mwdb/web && npx prettier --config package.json --write "../../docker/plugins/threatlib/**/*.{ts,tsx}" && cd ../..
git add docker/plugins/threatlib
git commit -m "threatlib: threat page with README editing, category change, unlink and delete

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Attribute renderer, docs and final checks

**Files:**
- Create: `docker/plugins/threatlib/components/ThreatNameAttribute.tsx`
- Modify: `docker/plugins/threatlib/index.tsx` (`attributeRenderers`)
- Modify: `docker/plugins/threatlib/README.md` (Frontend section)

**Interfaces:**
- Consumes: the `AttributeNodeProps` contract from `mwdb/web/src/components/ShowObject/common/Attributes.tsx`: `{attributeKey: string; attributes: Attribute[]; attributeDefinition: AttributeDefinition; onRemoveAttribute: (id: number) => void; onUpdateAttributes: () => void}`; `Attribute` has `id` and `value`.
- Produces: `ThreatNameAttribute` rendering one table row (`<tr><th>label</th><td>links…</td></tr>`) so it sits inside the attributes `DataTable` like the default renderer.

- [ ] **Step 1: Write the renderer**

`docker/plugins/threatlib/components/ThreatNameAttribute.tsx`:

```tsx
import { Link } from "react-router-dom";

import type { Attribute, AttributeDefinition } from "@mwdb-web/types/types";

type Props = {
    attributeKey: string;
    attributes: Attribute[];
    attributeDefinition: AttributeDefinition;
    onRemoveAttribute: (id: number) => void;
    onUpdateAttributes: () => void;
};

export function ThreatNameAttribute({ attributes, attributeDefinition }: Props) {
    return (
        <tr>
            <th>{attributeDefinition.label || "jpop_threat_name"}</th>
            <td>
                {attributes.map((attr) => {
                    const name = String(attr.value);
                    return (
                        <div key={attr.id}>
                            <Link to={`/threatlib/threat/${encodeURIComponent(name)}`}>
                                {name}
                            </Link>
                        </div>
                    );
                })}
            </td>
        </tr>
    );
}
```

- [ ] **Step 2: Register it and finalise index.tsx**

`docker/plugins/threatlib/index.tsx` final form:

```tsx
import { Link, Route } from "react-router-dom";

import { RequiresCapability } from "@mwdb-web/commons/ui";
import { Capability } from "@mwdb-web/types/types";

import { ThreatListView } from "./components/ThreatListView";
import { ThreatNameAttribute } from "./components/ThreatNameAttribute";
import { ThreatUploadView } from "./components/ThreatUploadView";
import { ThreatView } from "./components/ThreatView";

export default () => ({
    protectedRoutes: [
        <Route key="threatlib-list" path="threatlib" element={<ThreatListView />} />,
        <Route
            key="threatlib-upload"
            path="threatlib/upload"
            element={
                <RequiresCapability capability={Capability.addingFiles}>
                    <ThreatUploadView />
                </RequiresCapability>
            }
        />,
        <Route key="threatlib-threat" path="threatlib/threat/:name" element={<ThreatView />} />,
    ],
    navdropdownExtras: [
        <Link key="threatlib" className="dropdown-item" to="/threatlib">
            Threat library
        </Link>,
    ],
    attributeRenderers: {
        jpop_threat_name: ThreatNameAttribute,
    },
});
```

- [ ] **Step 3: Verify in the dev stack**

Open the sample page of a file linked to a threat: the `jpop_threat_name` row shows the name as a link that opens the threat page. `docker compose logs mwdb-web | grep -iE 'error' | tail` is clean. Run the jest helpers and prettier check:

```bash
cd mwdb/web && npx jest --roots ../../docker/plugins/threatlib --testMatch '**/*.test.ts' --modulePaths "$PWD/node_modules" && npx prettier --config package.json --check "../../docker/plugins/threatlib/**/*.{ts,tsx}"
```

Expected: tests pass, prettier reports all files formatted.

- [ ] **Step 4: Document**

Append to `docker/plugins/threatlib/README.md`:

```markdown
## Frontend

Pages (SPA routes, all under the authenticated group):

- `/threatlib` — list with name-prefix search, category filter and paging.
- `/threatlib/upload` — one form: threat (autocomplete; new names show category + README), file/folder drop with editable per-file paths, per-file result statuses. Requires `adding_files`.
- `/threatlib/threat/<name>` — README (rendered with marked + DOMPurify, editable), category change, sample table with unlink, delete. Controls follow capabilities.

Hooks in `index.tsx`: `protectedRoutes`, `navdropdownExtras` ("Threat library" under Extras), `attributeRenderers` (`jpop_threat_name` → link to the threat page).

Checks:

    cd mwdb/web && npx jest --roots ../../docker/plugins/threatlib --testMatch '**/*.test.ts' --modulePaths "$PWD/node_modules"
    cd mwdb/web && npx prettier --config package.json --check "../../docker/plugins/threatlib/**/*.{ts,tsx}"
```

- [ ] **Step 5: Commit**

```bash
git add docker/plugins/threatlib
git commit -m "threatlib: jpop_threat_name attribute renderer and frontend docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec §6 coverage.** List page → Task 3. Upload page (autocomplete, inline creation, folder drop with relative paths, result view) → Task 4; the "Strip top folder" button and editable paths resolve the ambiguity between "relative to the dropped folder" and the repo's `sources/<id>/...` layout by letting the researcher choose. Threat page (README render/edit, category change, sample table, unlink, delete, capability gating) → Task 5. Nav entry and attribute renderer → Tasks 1 and 6. No upstream files are modified.
- **Type consistency.** `api.ts` types match Part 1's JSON shapes (`threat_dict`, `sample_dict`, upload response). `Capability.addingFiles` / `Capability.removingObjects` are the enum members present in `mwdb/web/src/types/types.ts`.
- **Verification is stack-based** for components because the web jest config only matches `src/`; pure modules have jest tests run with explicit `--roots`.
