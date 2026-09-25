import { AxiosInstance } from "axios";

export const OPTION_KEYS = [
    "strip_comments",
    "execute_pure",
    "eval_peel",
    "remove_dead_code",
    "rename_vars",
    "analyze",
] as const;

export type OptionKey = (typeof OPTION_KEYS)[number];

export type DeobfOptions = Record<OptionKey, boolean>;

export const OPTION_LABELS: Record<OptionKey, { label: string; help: string }> =
    {
        strip_comments: {
            label: "Strip comments",
            help: "Remove all comments from the input before reducing.",
        },
        execute_pure: {
            label: "Execute pure decoder functions",
            help: "Run provably pure user functions in a sandboxed subprocess to resolve decoder calls the static reducer cannot.",
        },
        eval_peel: {
            label: "Peel eval() layers (sandbox)",
            help: "Run the sample under the php-eval-hook extension in a hardened sandbox and capture each eval() layer.",
        },
        remove_dead_code: {
            label: "Remove dead code",
            help: "Drop assignments to variables the reduced code never reads.",
        },
        rename_vars: {
            label: "Rename obfuscated variables",
            help: "Give obfuscated-looking variables readable names when the code proves their role.",
        },
        analyze: {
            label: "Security analysis",
            help: "List dangerous sources and sinks in the result and store them as searchable attributes.",
        },
    };

export type DeobfOk = {
    status: "ok";
    blob_id: string;
    created: boolean;
    peeled_blob_id: string | null;
    peeled_created: boolean | null;
    peeled_layers: number | null;
    analysis_blob_id: string | null;
    analysis_created: boolean | null;
    analysis_summary: {
        source_count: number;
        sink_count: number;
        auto_exec_count: number;
        categories_present: string[];
    } | null;
    warnings: { code: string; message: string }[];
    engine: { phpdeobf: string; evalhook_available: boolean };
    options: DeobfOptions;
    elapsed_ms: number;
};

export type DeobfError = {
    status: "error";
    code: string;
    message: string;
};

export type DeobfResult = DeobfOk | DeobfError;

export type DeobfBody =
    | { preset: "full" }
    | { preset: "static" }
    | { preset: "custom"; options: DeobfOptions };

/**
 * Trigger PHP deobfuscation of the sample identified by `sampleId`.
 *
 * Returns DeobfOk on success, DeobfError when the sidecar reports an
 * application-level failure (200 + status:"error"). Transport-level failures
 * (404, 413, 503) reach the caller as a thrown axios error and should be
 * handled in the caller's catch.
 */
export async function deobfuscate(
    api: AxiosInstance,
    sampleId: string,
    body: DeobfBody,
    signal?: AbortSignal
): Promise<DeobfResult> {
    const resp = await api.post<DeobfResult>(`/phpdeobf/${sampleId}`, body, {
        signal,
    });
    return resp.data;
}

export async function health(
    api: AxiosInstance,
    signal?: AbortSignal
): Promise<{ status: "ok"; evalhook_available: boolean; phpdeobf: string }> {
    const resp = await api.get<{
        status: "ok";
        evalhook_available: boolean;
        phpdeobf: string;
    }>("/phpdeobf/health", { signal });
    return resp.data;
}

/**
 * Client-side mirror of the backend's "full" preset, used only to pre-fill
 * the advanced options panel before the user touches anything. The backend
 * recomputes the same thing server-side when {preset: "full"} is sent.
 */
export function fullPreset(sampleHead: string): DeobfOptions {
    const opts = {} as DeobfOptions;
    for (const key of OPTION_KEYS) {
        opts[key] = true;
    }
    opts.eval_peel = sampleHead.toLowerCase().includes("eval");
    return opts;
}

export function isOk(r: DeobfResult): r is DeobfOk {
    return r.status === "ok";
}
