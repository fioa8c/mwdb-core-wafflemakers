import { OPTION_KEYS, OPTION_LABELS } from "../api";
import type { DeobfOptions, OptionKey } from "../api";

type Props = {
    value: DeobfOptions;
    onChange: (next: DeobfOptions) => void;
    evalhookAvailable: boolean;
    disabled: boolean;
    onRun: () => void;
};

export function OptionsPanel({
    value,
    onChange,
    evalhookAvailable,
    disabled,
    onRun,
}: Props) {
    return (
        <div className="card mt-2">
            <div className="card-body">
                {OPTION_KEYS.map((key: OptionKey) => {
                    const blocked = key === "eval_peel" && !evalhookAvailable;
                    return (
                        <div
                            className="form-check mb-1"
                            key={key}
                            title={
                                blocked
                                    ? "The eval-hook extension is not available in this sidecar"
                                    : undefined
                            }
                        >
                            <input
                                className="form-check-input"
                                type="checkbox"
                                id={`opt-${key}`}
                                checked={value[key] && !blocked}
                                disabled={disabled || blocked}
                                onChange={(ev) =>
                                    onChange({
                                        ...value,
                                        [key]: ev.target.checked,
                                    })
                                }
                            />
                            <label
                                className="form-check-label"
                                htmlFor={`opt-${key}`}
                            >
                                {OPTION_LABELS[key].label}
                                <small className="d-block text-muted">
                                    {OPTION_LABELS[key].help}
                                </small>
                            </label>
                        </div>
                    );
                })}
                <button
                    type="button"
                    className="btn btn-outline-primary btn-sm mt-2"
                    disabled={disabled}
                    onClick={onRun}
                >
                    Run with these options
                </button>
            </div>
        </div>
    );
}
