import type { Diagnostic } from "../api";

type Props = {
    diagnostics: Diagnostic[];
    inFlight: boolean;
    matchCount: number | null; // null = no result yet
};

/**
 * Strip directly under the regex input. Shows engine diagnostics, an
 * "evaluating..." indicator while a request is in flight, and a match
 * count when the last result was successful.
 */
export function LintStrip({ diagnostics, inFlight, matchCount }: Props) {
    const hasError = diagnostics.some((d) => d.severity === "error");

    return (
        <div
            className="d-flex flex-wrap align-items-start"
            style={{
                gap: "12px",
                padding: "6px 12px",
                fontSize: "12px",
                background: "#fffbe6",
                borderBottom: "1px solid #ffe7a0",
                fontFamily:
                    'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
            }}
        >
            {!hasError && matchCount !== null && (
                <span style={{ color: "#28a745" }}>
                    ✓ valid · {matchCount} match{matchCount === 1 ? "" : "es"}
                </span>
            )}
            {inFlight && (
                <span style={{ color: "#888" }}>evaluating…</span>
            )}
            {diagnostics.map((d, i) => {
                const color =
                    d.severity === "error"
                        ? "#dc3545"
                        : d.severity === "warning"
                          ? "#b8860b"
                          : "#0c5460";
                const icon =
                    d.severity === "error"
                        ? "✗"
                        : d.severity === "warning"
                          ? "⚠"
                          : "ⓘ";
                return (
                    <span
                        key={i}
                        style={{ color, whiteSpace: "pre-wrap" }}
                        title={d.code}
                    >
                        {icon} {d.message}
                    </span>
                );
            })}
        </div>
    );
}
