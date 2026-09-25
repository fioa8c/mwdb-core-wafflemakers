import { Link } from "react-router-dom";

import { makeSearchLink } from "@mwdb-web/commons/helpers";

import type { DeobfOk } from "../api";

export function AnalysisPanel({ result }: { result: DeobfOk }) {
    const s = result.analysis_summary;
    if (!s) return null;
    return (
        <div className="card mt-3">
            <div className="card-header">Security analysis</div>
            <div className="card-body">
                <div className="mb-2">
                    <strong>{s.source_count}</strong> source(s),{" "}
                    <strong>{s.sink_count}</strong> sink(s),{" "}
                    <strong>{s.auto_exec_count}</strong> auto-executing
                </div>
                <div className="mb-2">
                    {s.categories_present.length === 0 && (
                        <span className="text-muted">
                            No dangerous sinks found.
                        </span>
                    )}
                    {s.categories_present.map((c) => (
                        <Link
                            key={c}
                            className="badge badge-danger mr-1"
                            to={makeSearchLink({
                                field: "attribute.phpdeobf_sink",
                                value: c,
                                pathname: "/search",
                            })}
                            title={`Search all samples with sink ${c}`}
                        >
                            {c}
                        </Link>
                    ))}
                </div>
                {result.analysis_blob_id && (
                    <small>
                        Full report:{" "}
                        <Link to={`/blob/${result.analysis_blob_id}`}>
                            {result.analysis_blob_id.slice(0, 12)}…
                        </Link>
                    </small>
                )}
            </div>
        </div>
    );
}
