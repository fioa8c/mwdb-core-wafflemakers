import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { APIContext } from "@mwdb-web/commons/api";
import { negateBuffer } from "@mwdb-web/commons/helpers";
import { ObjectContext } from "@mwdb-web/components/ShowObject";
import type { ObjectData } from "@mwdb-web/types/types";

import { deobfuscate, fullPreset, health as fetchHealth, isOk } from "../api";
import type { DeobfBody, DeobfOptions, DeobfResult } from "../api";
import { AnalysisPanel } from "./AnalysisPanel";
import { OptionsPanel } from "./OptionsPanel";

const MAX_SAMPLE_SIZE = 5 * 1024 * 1024;

type State =
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "done"; result: DeobfResult }
    | { kind: "unavailable" };

type HealthState =
    | null
    | "unavailable"
    | { status: "ok"; evalhook_available: boolean; phpdeobf: string };

// eval_peel_error is intentionally absent here — its own message is shown
// verbatim (see the warnings render below).
const WARNING_TEXT: Record<string, string> = {
    eval_peel_unavailable:
        "Peeling skipped: the eval-hook extension is not available in this sidecar.",
    eval_peel_no_layers:
        "Peeling captured no eval() layers; showing static output.",
    eval_peel_timeout: "Peeling timed out; showing static output.",
};

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

function isAbort(err: { name?: string }): boolean {
    return err.name === "CanceledError" || err.name === "AbortError";
}

export function PhpDeobfTab() {
    const api = useContext(APIContext);
    const objectContext = useContext(ObjectContext);
    const object = objectContext?.object as Partial<ObjectData> | undefined;
    const sampleId = object?.id ?? "";
    const fileName = object?.file_name;
    const fileSize = object?.file_size;

    const [state, setState] = useState<State>({ kind: "idle" });
    const [sampleHead, setSampleHead] = useState<string>("");
    const [runAnyway, setRunAnyway] = useState(false);
    const [healthState, setHealthState] = useState<HealthState>(null);
    const [advanced, setAdvanced] = useState(false);
    const [options, setOptions] = useState<DeobfOptions>(() => fullPreset(""));
    const optionsTouchedRef = useRef(false);
    const abortRef = useRef<AbortController | null>(null);

    const evalhookAvailable =
        healthState !== null &&
        healthState !== "unavailable" &&
        healthState.evalhook_available;

    // Abort any in-flight request when the component unmounts or the sample changes,
    // so a late response doesn't try to setState on an unmounted component.
    useEffect(() => {
        return () => {
            abortRef.current?.abort();
        };
    }, [sampleId]);

    // Fetch sidecar health (engine version, eval-hook availability) on mount.
    useEffect(() => {
        const ctrl = new AbortController();
        (async () => {
            try {
                const h = await fetchHealth(api.axios, ctrl.signal);
                if (ctrl.signal.aborted) return;
                setHealthState(h);
            } catch (e: unknown) {
                if (ctrl.signal.aborted) return;
                if (isAbort(e as { name?: string })) return;
                setHealthState("unavailable");
            }
        })();
        return () => ctrl.abort();
    }, [api]);

    // Fetch a small slice of the sample for the heuristic check on mount.
    useEffect(() => {
        if (!sampleId) return;
        let cancelled = false;
        (async () => {
            try {
                const resp = await api.downloadFile(sampleId, 1);
                const bytes = negateBuffer(resp.data);
                const text = new TextDecoder("utf-8", { fatal: false }).decode(
                    bytes.slice(0, 2048)
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

    // Pre-fill the advanced options panel from the full preset once the sample
    // head (and engine health) are known, unless the user has already touched
    // a switch themselves — a later refinement of either shouldn't clobber
    // their choices.
    useEffect(() => {
        if (optionsTouchedRef.current) return;
        const preset = fullPreset(sampleHead);
        if (!evalhookAvailable) preset.eval_peel = false;
        setOptions(preset);
    }, [sampleHead, evalhookAvailable]);

    const isPhpLike = useMemo(
        () => looksLikePhp(sampleHead, fileName),
        [sampleHead, fileName]
    );
    const isOversize =
        typeof fileSize === "number" && fileSize > MAX_SAMPLE_SIZE;
    const buttonDisabled =
        isOversize || state.kind === "running" || (!isPhpLike && !runAnyway);

    async function runWith(body: DeobfBody) {
        abortRef.current?.abort();
        const ctrl = new AbortController();
        abortRef.current = ctrl;
        setState({ kind: "running" });
        try {
            const result = await deobfuscate(
                api.axios,
                sampleId,
                body,
                ctrl.signal
            );
            if (ctrl.signal.aborted) return;
            setState({ kind: "done", result });
        } catch (e: unknown) {
            if (ctrl.signal.aborted) return;
            const err = e as {
                name?: string;
                response?: { status?: number; data?: { message?: string } };
                message?: string;
            };
            if (isAbort(err)) {
                return;
            }
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

    function onRun() {
        runWith({ preset: "full" });
    }

    function onRunAdvanced() {
        const opts = evalhookAvailable
            ? options
            : { ...options, eval_peel: false };
        runWith({ preset: "custom", options: opts });
    }

    function onOptionsChange(next: DeobfOptions) {
        optionsTouchedRef.current = true;
        setOptions(next);
    }

    return (
        <div style={{ padding: "20px", maxWidth: "800px" }}>
            <h4>PHP Deobfuscator</h4>
            <div className="mb-2">
                <small className="text-muted">
                    {healthState === null && "Checking engine…"}
                    {healthState === "unavailable" && "Engine unavailable"}
                    {healthState !== null && healthState !== "unavailable" && (
                        <>
                            Engine {healthState.phpdeobf} · eval-hook{" "}
                            {healthState.evalhook_available
                                ? "available"
                                : "unavailable"}
                        </>
                    )}
                </small>
            </div>
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
            </button>{" "}
            <button
                type="button"
                className="btn btn-link btn-sm"
                onClick={() => setAdvanced((v) => !v)}
            >
                {advanced ? "Hide advanced options" : "Advanced"}
            </button>
            {advanced && (
                <OptionsPanel
                    value={options}
                    onChange={onOptionsChange}
                    evalhookAvailable={evalhookAvailable}
                    disabled={buttonDisabled}
                    onRun={onRunAdvanced}
                />
            )}
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
                    <>
                        <div className="mb-2">
                            <strong>{state.result.elapsed_ms} ms</strong>
                            {" · "}
                            static output
                            {state.result.peeled_blob_id && " · peeled output"}
                            {state.result.analysis_blob_id && " · analysis"}
                        </div>

                        {state.result.warnings.map((w, i) => (
                            <div
                                className="alert alert-warning"
                                key={`${w.code}-${i}`}
                            >
                                {WARNING_TEXT[w.code] ?? w.message}
                            </div>
                        ))}

                        <div className="mb-2">
                            {state.result.created
                                ? "Created child blob "
                                : "Already deobfuscated — opening existing child blob: "}
                            <Link to={`/blob/${state.result.blob_id}`}>
                                {shortHash(state.result.blob_id)}
                            </Link>
                        </div>

                        {state.result.peeled_blob_id && (
                            <div className="mb-2">
                                {state.result.peeled_created
                                    ? "Created peeled blob "
                                    : "Already peeled — opening existing peeled blob: "}
                                <Link
                                    to={`/blob/${state.result.peeled_blob_id}`}
                                >
                                    {shortHash(state.result.peeled_blob_id)}
                                </Link>
                                {state.result.peeled_layers !== null &&
                                    ` (${state.result.peeled_layers} layer(s))`}
                            </div>
                        )}

                        <AnalysisPanel result={state.result} />
                    </>
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
