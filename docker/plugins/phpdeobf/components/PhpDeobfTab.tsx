import { useContext, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { APIContext } from "@mwdb-web/commons/api";
import { negateBuffer } from "@mwdb-web/commons/helpers";
import { ObjectContext } from "@mwdb-web/components/ShowObject";
import type { ObjectData } from "@mwdb-web/types/types";

import { deobfuscate, isOk } from "../api";
import type { DeobfResult } from "../api";

const MAX_SAMPLE_SIZE = 5 * 1024 * 1024;

type State =
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "done"; result: DeobfResult }
    | { kind: "unavailable" };

/** Heuristic: does this sample look like PHP? */
function looksLikePhp(text: string, fileName: string | undefined): boolean {
    const head = text.slice(0, 2048);
    if (head.includes("<?php") || head.includes("<?")) return true;
    if (fileName && /\.php$/i.test(fileName)) return true;
    return false;
}

function shortHash(h: string): string {
    return h.length > 12 ? `${h.slice(0, 8)}…${h.slice(-4)}` : h;
}

export function PhpDeobfTab() {
    const api = useContext(APIContext);
    const objectContext = useContext(ObjectContext);
    const object = objectContext?.object as Partial<ObjectData> | undefined;
    const sampleId = object?.id ?? "";
    const fileName = (object as { file_name?: string } | undefined)?.file_name;
    const fileSize = (object as { file_size?: number } | undefined)?.file_size;

    const [state, setState] = useState<State>({ kind: "idle" });
    const [sampleHead, setSampleHead] = useState<string>("");
    const [runAnyway, setRunAnyway] = useState(false);

    // Fetch a small slice of the sample for the heuristic check on mount.
    useEffect(() => {
        if (!sampleId) return;
        let cancelled = false;
        (async () => {
            try {
                const resp = await api.downloadFile(sampleId, 1);
                const bytes = negateBuffer(resp.data);
                const text = new TextDecoder("utf-8", { fatal: false }).decode(
                    bytes.slice(0, 2048),
                );
                if (!cancelled) setSampleHead(text);
            } catch {
                // If we can't peek, default the heuristic to "doesn't look like PHP";
                // the user can still tick "run anyway".
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [api, sampleId]);

    const isPhpLike = useMemo(
        () => looksLikePhp(sampleHead, fileName),
        [sampleHead, fileName],
    );
    const isOversize =
        typeof fileSize === "number" && fileSize > MAX_SAMPLE_SIZE;
    const buttonDisabled =
        isOversize ||
        state.kind === "running" ||
        (!isPhpLike && !runAnyway);

    async function onRun() {
        setState({ kind: "running" });
        try {
            const result = await deobfuscate(api.axios, sampleId);
            setState({ kind: "done", result });
        } catch (e: unknown) {
            const err = e as {
                response?: { status?: number; data?: { message?: string } };
                message?: string;
            };
            if (err.response?.status === 503) {
                setState({ kind: "unavailable" });
                return;
            }
            const message =
                err.response?.data?.message ?? err.message ?? "Request failed.";
            setState({
                kind: "done",
                result: {
                    status: "error",
                    code: `http_${err.response?.status ?? "unknown"}`,
                    message,
                },
            });
        }
    }

    return (
        <div style={{ padding: "20px", maxWidth: "800px" }}>
            <h4>PHP Deobfuscator</h4>

            {!isPhpLike && (
                <div
                    style={{
                        background: "#fff3cd",
                        border: "1px solid #ffeeba",
                        padding: "8px 12px",
                        marginBottom: "12px",
                        borderRadius: "4px",
                    }}
                >
                    This sample doesn&apos;t look like PHP.{" "}
                    <label style={{ marginLeft: "6px" }}>
                        <input
                            type="checkbox"
                            checked={runAnyway}
                            onChange={(e) => setRunAnyway(e.target.checked)}
                        />{" "}
                        Run anyway
                    </label>
                </div>
            )}

            {isOversize && (
                <div
                    style={{
                        background: "#f8d7da",
                        border: "1px solid #f5c6cb",
                        padding: "8px 12px",
                        marginBottom: "12px",
                        borderRadius: "4px",
                    }}
                >
                    Sample is larger than {MAX_SAMPLE_SIZE} bytes; the
                    deobfuscator only accepts samples up to that size.
                </div>
            )}

            <button
                type="button"
                className="btn btn-primary"
                disabled={buttonDisabled}
                onClick={onRun}
            >
                {state.kind === "running"
                    ? "Running…"
                    : state.kind === "done"
                      ? "Run again"
                      : "Deobfuscate"}
            </button>

            <div style={{ marginTop: "16px" }}>
                {state.kind === "running" && <em>Running deobfuscator…</em>}

                {state.kind === "unavailable" && (
                    <div
                        style={{
                            background: "#f8d7da",
                            border: "1px solid #f5c6cb",
                            padding: "10px 14px",
                            borderRadius: "4px",
                            color: "#721c24",
                        }}
                    >
                        PHP deobfuscator backend unavailable. Please contact
                        your administrator.
                    </div>
                )}

                {state.kind === "done" && isOk(state.result) && (
                    <div
                        style={{
                            background: "#d4edda",
                            border: "1px solid #c3e6cb",
                            padding: "10px 14px",
                            borderRadius: "4px",
                            color: "#155724",
                        }}
                    >
                        {state.result.created
                            ? "Created child blob "
                            : "Already deobfuscated — opening existing child blob: "}
                        <Link to={`/blob/${state.result.blob_id}`}>
                            {shortHash(state.result.blob_id)}
                        </Link>
                        <span
                            style={{
                                color: "#155724aa",
                                marginLeft: "10px",
                                fontSize: "0.9em",
                            }}
                        >
                            ({state.result.elapsed_ms} ms)
                        </span>
                    </div>
                )}

                {state.kind === "done" && !isOk(state.result) && (
                    <div
                        style={{
                            background: "#f8d7da",
                            border: "1px solid #f5c6cb",
                            padding: "10px 14px",
                            borderRadius: "4px",
                            color: "#721c24",
                        }}
                    >
                        <div
                            style={{
                                fontSize: "0.8em",
                                textTransform: "uppercase",
                                letterSpacing: "1px",
                                marginBottom: "4px",
                                opacity: 0.7,
                            }}
                        >
                            {state.result.code}
                        </div>
                        <pre
                            style={{
                                whiteSpace: "pre-wrap",
                                margin: 0,
                                fontFamily: "inherit",
                            }}
                        >
                            {state.result.message}
                        </pre>
                    </div>
                )}
            </div>
        </div>
    );
}
