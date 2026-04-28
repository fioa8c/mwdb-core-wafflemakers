import { useEffect, useRef } from "react";

type Props = {
    sampleId: string;
    value: string;
    onChange: (next: string) => void;
};

const STORAGE_KEY_PREFIX = "yarax-regex-draft:";

/**
 * Single-line monospace regex input with per-sample localStorage draft.
 *
 * On mount: hydrates `value` from localStorage if the parent is still at
 * its initial empty state. (Parent owns the value; this component is a
 * controlled input with a side effect for persistence.)
 *
 * On change: writes to localStorage on every keystroke. The 200ms
 * debounce lives at the parent level on the network call — local
 * persistence is fast enough to do eagerly.
 */
export function RegexInput({ sampleId, value, onChange }: Props) {
    const inputRef = useRef<HTMLInputElement>(null);
    const hydrated = useRef(false);

    useEffect(() => {
        // One-shot hydration on first render, only if parent is still empty
        // and we haven't already hydrated for this sampleId.
        if (hydrated.current) return;
        hydrated.current = true;
        if (value === "") {
            const saved = localStorage.getItem(STORAGE_KEY_PREFIX + sampleId);
            if (saved !== null && saved !== "") {
                onChange(saved);
            }
        }
        inputRef.current?.focus();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sampleId]);

    useEffect(() => {
        // Persist on every change, but skip the very first render to avoid
        // overwriting saved state with the initial empty parent value.
        if (!hydrated.current) return;
        if (value === "") {
            localStorage.removeItem(STORAGE_KEY_PREFIX + sampleId);
        } else {
            localStorage.setItem(STORAGE_KEY_PREFIX + sampleId, value);
        }
    }, [sampleId, value]);

    return (
        <input
            ref={inputRef}
            type="text"
            spellCheck={false}
            autoComplete="off"
            className="form-control"
            style={{
                fontFamily:
                    'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
                fontSize: "13px",
            }}
            placeholder="YARA-X regex (e.g. \\$[a-z_\\x80-\\xff][a-z0-9_\\x80-\\xff]*)"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            aria-label="YARA-X regex pattern"
        />
    );
}
