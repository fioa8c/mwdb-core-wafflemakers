import { AxiosInstance } from "axios";

export type DeobfOk = {
    status: "ok";
    blob_id: string;
    created: boolean;
    elapsed_ms: number;
};

export type DeobfError = {
    status: "error";
    code: string;
    message: string;
};

export type DeobfResult = DeobfOk | DeobfError;

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
    signal?: AbortSignal,
): Promise<DeobfResult> {
    const resp = await api.post<DeobfResult>(
        `/phpdeobf/${sampleId}`,
        {},
        { signal },
    );
    return resp.data;
}

export function isOk(r: DeobfResult): r is DeobfOk {
    return r.status === "ok";
}
