import { useContext, useEffect, useRef, useState } from "react";

import { APIContext } from "@mwdb-web/commons/api";
import { negateBuffer } from "@mwdb-web/commons/helpers";
import { ObjectContext } from "@mwdb-web/components/ShowObject";
import type { ObjectData } from "@mwdb-web/types/types";

import { evalRegex, isOk } from "../api";
import type { Diagnostic, Match, RegexResult } from "../api";
import { LintStrip } from "./LintStrip";
import { MatchList } from "./MatchList";
import { RegexInput } from "./RegexInput";
import {
    SampleWithHighlights,
    SampleWithHighlightsHandle,
} from "./SampleWithHighlights";

const DEBOUNCE_MS = 200;

/**
 * Top-level YARA-X regex tab. Renders inside an MWDB ObjectTab.
 *
 * Owns:
 *   - regex draft (initialized empty; RegexInput hydrates from localStorage)
 *   - last successful result (matches + diagnostics from last 200 OK ok-status)
 *   - last full result (last 200 OK regardless of status; drives lint strip)
 *   - in-flight tracking + debounce timer + AbortController for request cancellation
 *   - sample bytes (fetched once on mount)
 */
export function YaraXRegexTab() {
    const api = useContext(APIContext);
    const objectContext = useContext(ObjectContext);
    // ObjectContext.object is Partial<ObjectOrConfigOrBlobData>; the YARA-X
    // tab only mounts on file objects, so a Partial<ObjectData> view is the
    // honest narrowing — `.id` is on every variant either way.
    const object = objectContext?.object as Partial<ObjectData> | undefined;
    const sampleId = object?.id ?? "";

    const [regex, setRegex] = useState("");
    const [lastResult, setLastResult] = useState<RegexResult | null>(null);
    // The "successful" result is the last one whose status was "ok" — drives
    // the dimmed-stale UX when the current regex breaks.
    const [lastOkResult, setLastOkResult] = useState<
        Extract<RegexResult, { status: "ok" }> | null
    >(null);
    const [inFlight, setInFlight] = useState(false);

    const [sampleText, setSampleText] = useState<string>("");
    const [sampleError, setSampleError] = useState<string | null>(null);

    const debounceRef = useRef<number | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const sampleRef = useRef<SampleWithHighlightsHandle>(null);

    // Fetch the sample bytes once on mount (or when sample_id changes).
    useEffect(() => {
        if (!sampleId) return;
        let cancelled = false;
        (async () => {
            try {
                const resp = await api.downloadFile(sampleId, 1);
                const bytes = negateBuffer(resp.data);
                const text = new TextDecoder("utf-8", { fatal: false }).decode(
                    bytes,
                );
                if (!cancelled) {
                    setSampleText(text);
                    setSampleError(null);
                }
            } catch (e) {
                if (!cancelled) {
                    setSampleError("Failed to load sample.");
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [api, sampleId]);

    // Debounced eval on regex change.
    useEffect(() => {
        if (!sampleId) return;
        if (regex === "") {
            // No call. Clear in-flight state. Keep lastOkResult so the user
            // can briefly clear the input and come back without losing
            // their previous highlights.
            if (debounceRef.current !== null) {
                window.clearTimeout(debounceRef.current);
                debounceRef.current = null;
            }
            if (abortRef.current) {
                abortRef.current.abort();
                abortRef.current = null;
            }
            setInFlight(false);
            setLastResult(null);
            return;
        }

        if (debounceRef.current !== null) {
            window.clearTimeout(debounceRef.current);
        }
        debounceRef.current = window.setTimeout(async () => {
            if (abortRef.current) abortRef.current.abort();
            const ctrl = new AbortController();
            abortRef.current = ctrl;
            setInFlight(true);
            try {
                const result = await evalRegex(
                    api.axios,
                    { sample_id: sampleId, regex },
                    ctrl.signal,
                );
                setLastResult(result);
                if (isOk(result)) {
                    setLastOkResult(result);
                }
            } catch (e: any) {
                if (e?.name === "CanceledError" || e?.name === "AbortError") {
                    return; // superseded by a newer regex
                }
                setLastResult({
                    status: "compile_error",
                    diagnostics: [
                        {
                            severity: "error",
                            code: "request_failed",
                            message:
                                e?.response?.data?.message ||
                                e?.message ||
                                "Request failed.",
                        },
                    ],
                });
            } finally {
                if (abortRef.current === ctrl) setInFlight(false);
            }
        }, DEBOUNCE_MS);

        return () => {
            if (debounceRef.current !== null) {
                window.clearTimeout(debounceRef.current);
            }
            // Also abort any in-flight request — covers unmount and the
            // sampleId-changed case where the old response would otherwise
            // land on the new sample's state.
            abortRef.current?.abort();
        };
    }, [api, regex, sampleId]);

    const isStale =
        lastResult !== null &&
        lastResult.status !== "ok" &&
        lastOkResult !== null;
    const matches: Match[] =
        lastResult && isOk(lastResult)
            ? lastResult.matches
            : isStale && lastOkResult
              ? lastOkResult.matches
              : [];
    const diagnostics: Diagnostic[] = lastResult
        ? lastResult.diagnostics
        : [];
    const matchCount: number | null =
        lastResult && isOk(lastResult) ? lastResult.matches.length : null;

    if (sampleError) {
        return (
            <div style={{ padding: "20px", color: "#dc3545" }}>
                {sampleError}
            </div>
        );
    }

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                minHeight: "70vh",
            }}
        >
            <div style={{ padding: "10px 14px", background: "#fafafa" }}>
                <RegexInput
                    sampleId={sampleId}
                    value={regex}
                    onChange={setRegex}
                />
            </div>
            <LintStrip
                diagnostics={diagnostics}
                inFlight={inFlight}
                matchCount={matchCount}
            />
            <div
                style={{
                    display: "flex",
                    flex: 1,
                    minHeight: 0,
                    borderTop: "1px solid #eee",
                }}
            >
                <div style={{ flex: 1, display: "flex", minWidth: 0 }}>
                    <SampleWithHighlights
                        ref={sampleRef}
                        sampleText={sampleText}
                        matches={matches}
                        dimmed={isStale}
                    />
                </div>
                <div
                    style={{
                        width: "240px",
                        background: "#f8f9fa",
                        borderLeft: "1px solid #eee",
                        display: "flex",
                        flexDirection: "column",
                    }}
                >
                    <h4
                        style={{
                            fontSize: "10px",
                            textTransform: "uppercase",
                            color: "#888",
                            letterSpacing: "1px",
                            margin: "10px 14px 6px",
                        }}
                    >
                        Matches{" "}
                        {matches.length > 0 && (
                            <span style={{ color: "#444" }}>
                                ({matches.length})
                            </span>
                        )}
                    </h4>
                    <MatchList
                        matches={matches}
                        dimmed={isStale}
                        onJump={(m) => sampleRef.current?.jumpTo(m)}
                    />
                </div>
            </div>
        </div>
    );
}
