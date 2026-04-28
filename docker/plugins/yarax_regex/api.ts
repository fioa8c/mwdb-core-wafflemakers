import { AxiosInstance } from "axios";

export type Match = {
    offset: number;
    length: number;
    line: number;
    column: number;
    text: string;
};

export type Diagnostic = {
    severity: "error" | "warning" | "info";
    code: string;
    message: string;
};

export type RegexResult =
    | {
          status: "ok";
          matches: Match[];
          diagnostics: Diagnostic[];
          atoms: string[];
          elapsed_ms: number;
      }
    | {
          status: "compile_error";
          diagnostics: Diagnostic[];
      }
    | {
          status: "sample_too_large";
          diagnostics: Diagnostic[];
      }
    | {
          status: "scan_timeout";
          diagnostics: Diagnostic[];
      };

export type RegexRequest = {
    sample_id: string;
    regex: string;
};

/**
 * Evaluate `regex` against the sample identified by `sample_id` via the
 * MWDB yarax-regex plugin endpoint.
 *
 * Pass an `AbortSignal` from the caller's debounce loop so stale requests
 * (regex value changed before this one returned) can be cancelled.
 */
export async function evalRegex(
    api: AxiosInstance,
    req: RegexRequest,
    signal?: AbortSignal,
): Promise<RegexResult> {
    const response = await api.post<RegexResult>("/yarax/regex", req, { signal });
    return response.data;
}

/**
 * True if the result has matches/atoms/elapsed_ms (i.e. status was "ok").
 * Narrows the union for TS consumers.
 */
export function isOk(
    r: RegexResult,
): r is Extract<RegexResult, { status: "ok" }> {
    return r.status === "ok";
}
