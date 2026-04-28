import { forwardRef, useImperativeHandle, useMemo, useRef } from "react";

import type { Match } from "../api";

type Props = {
    sampleText: string;
    matches: Match[];
    dimmed: boolean;
};

export type SampleWithHighlightsHandle = {
    /** Scroll the given match into view and pulse it briefly. */
    jumpTo: (match: Match) => void;
};

type Segment =
    | { kind: "text"; text: string }
    | { kind: "match"; text: string; matchIndex: number };

/**
 * Walk the sample once and produce alternating text/match segments.
 * Overlapping or touching matches are merged into one highlight span;
 * the click target still maps to the first contributing match.
 */
function buildSegments(sampleText: string, matches: Match[]): Segment[] {
    if (matches.length === 0) return [{ kind: "text", text: sampleText }];

    // Convert byte offsets to character offsets. The sample is a JS string
    // (decoded from UTF-8 bytes by the API layer); a multi-byte UTF-8
    // codepoint occupies 1 char in JS but multiple bytes in the offset.
    // For v0 we approximate: assume ASCII (web threats are usually ASCII
    // or UTF-8 ASCII-compatible). Non-ASCII shifts are a v1 concern; flag
    // it here as a known limitation.
    const sorted = [...matches]
        .map((m, i) => ({ ...m, _i: i }))
        .sort((a, b) => a.offset - b.offset);

    const segments: Segment[] = [];
    let cursor = 0;
    for (const m of sorted) {
        if (m.offset < cursor) continue; // overlap with previous; skip
        if (m.offset > cursor) {
            segments.push({
                kind: "text",
                text: sampleText.slice(cursor, m.offset),
            });
        }
        segments.push({
            kind: "match",
            text: sampleText.slice(m.offset, m.offset + m.length),
            matchIndex: m._i,
        });
        cursor = m.offset + m.length;
    }
    if (cursor < sampleText.length) {
        segments.push({ kind: "text", text: sampleText.slice(cursor) });
    }
    return segments;
}

export const SampleWithHighlights = forwardRef<
    SampleWithHighlightsHandle,
    Props
>(function SampleWithHighlights({ sampleText, matches, dimmed }, ref) {
    const containerRef = useRef<HTMLPreElement>(null);
    const markRefs = useRef<Map<number, HTMLElement>>(new Map());

    const segments = useMemo(
        () => buildSegments(sampleText, matches),
        [sampleText, matches],
    );

    useImperativeHandle(ref, () => ({
        jumpTo: (match: Match) => {
            const idx = matches.findIndex(
                (m) => m.offset === match.offset && m.length === match.length,
            );
            if (idx === -1) return;
            const el = markRefs.current.get(idx);
            if (!el) return;
            el.scrollIntoView({ behavior: "smooth", block: "center" });
            // Pulse: toggle a class for ~700ms
            el.classList.add("yarax-pulse");
            window.setTimeout(() => el.classList.remove("yarax-pulse"), 700);
        },
    }));

    return (
        <>
            <style>{`
                .yarax-mark {
                    background: #fff3a3;
                    border-bottom: 2px solid #b8860b;
                    padding: 1px 0;
                }
                .yarax-mark.yarax-pulse {
                    animation: yarax-pulse-kf 700ms ease;
                }
                @keyframes yarax-pulse-kf {
                    0%   { background: #ffd700; }
                    100% { background: #fff3a3; }
                }
            `}</style>
            <pre
                ref={containerRef}
                style={{
                    margin: 0,
                    padding: "10px 14px",
                    fontFamily:
                        'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
                    fontSize: "12px",
                    lineHeight: "1.6",
                    background: "#fafafa",
                    color: dimmed ? "rgba(34,34,34,0.5)" : "#222",
                    flex: 1,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    transition: "color 100ms ease",
                }}
            >
                {segments.map((seg, i) =>
                    seg.kind === "text" ? (
                        <span key={i}>{seg.text}</span>
                    ) : (
                        <mark
                            key={i}
                            ref={(el) => {
                                if (el) markRefs.current.set(seg.matchIndex, el);
                                else markRefs.current.delete(seg.matchIndex);
                            }}
                            className="yarax-mark"
                            style={{ opacity: dimmed ? 0.5 : 1 }}
                        >
                            {seg.text}
                        </mark>
                    ),
                )}
            </pre>
        </>
    );
});
