import type { Match } from "../api";

type Props = {
    matches: Match[];
    dimmed: boolean; // true when showing a stale result (regex broke)
    onJump: (match: Match) => void;
};

const MAX_TEXT_PREVIEW = 40;

function truncate(s: string): string {
    if (s.length <= MAX_TEXT_PREVIEW) return s;
    return s.slice(0, MAX_TEXT_PREVIEW - 1) + "…";
}

/**
 * Sidebar list of matches. Click a row → parent scrolls/jumps the
 * sample view to that line. When `dimmed` is true (stale result), the
 * whole list renders at 50% opacity to signal the data is from a
 * previous successful eval.
 */
export function MatchList({ matches, dimmed, onJump }: Props) {
    const wrapperStyle: React.CSSProperties = {
        opacity: dimmed ? 0.5 : 1,
        transition: "opacity 100ms ease",
        overflow: "auto",
        flex: 1,
    };

    if (matches.length === 0) {
        return (
            <div style={wrapperStyle}>
                <div
                    style={{
                        padding: "12px",
                        color: "#888",
                        fontSize: "12px",
                    }}
                >
                    No matches.
                </div>
            </div>
        );
    }

    return (
        <div style={wrapperStyle}>
            {matches.map((m, i) => (
                <button
                    key={`${m.offset}-${i}`}
                    type="button"
                    onClick={() => onJump(m)}
                    title={m.text}
                    style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        padding: "5px 12px",
                        border: "none",
                        borderBottom: "1px solid #eee",
                        background: "transparent",
                        fontFamily:
                            'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
                        fontSize: "11px",
                        cursor: "pointer",
                    }}
                    onMouseOver={(e) =>
                        (e.currentTarget.style.background = "#fff3a3")
                    }
                    onMouseOut={(e) =>
                        (e.currentTarget.style.background = "transparent")
                    }
                >
                    <span style={{ color: "#888", marginRight: "10px" }}>
                        L{m.line}:{m.column}
                    </span>
                    <span style={{ color: "#b8860b", fontWeight: "bold" }}>
                        {truncate(m.text)}
                    </span>
                </button>
            ))}
        </div>
    );
}
