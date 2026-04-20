import { useContext, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { faCode } from "@fortawesome/free-solid-svg-icons";
import { APIContext } from "@mwdb-web/commons/api";
import { ObjectContext } from "@mwdb-web/commons/context";
import { ObjectAction, ObjectTab, ObjectPreview } from "@mwdb-web/commons/ui";
import { useRemotePath } from "@mwdb-web/commons/remotes";
import { BlobData } from "@mwdb-web/types/types";

type AnalysisBlobs = {
    normalized: BlobData | null;
    evalhook: BlobData | null;
};

export function NormalizedCodeTab() {
    const api = useContext(APIContext);
    const context = useContext(ObjectContext);
    const remotePath = useRemotePath();
    const [blobs, setBlobs] = useState<AnalysisBlobs>({
        normalized: null,
        evalhook: null,
    });
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        async function fetchAnalysisBlobs() {
            if (!context.object?.id) return;
            try {
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                const relationsResp = await api.getObjectRelations(
                    context.object.id as any
                );
                const children = relationsResp.data.children || [];
                const blobChildren = children.filter(
                    (c: { type: string }) => c.type === "text_blob"
                );

                const result: AnalysisBlobs = {
                    normalized: null,
                    evalhook: null,
                };
                for (const child of blobChildren) {
                    const blobResp = await api.getObject("blob", child.id);
                    const blobData = blobResp.data as BlobData;
                    if (blobData.blob_type === "normalized-php") {
                        result.normalized = blobData;
                    } else if (blobData.blob_type === "evalhook-output") {
                        result.evalhook = blobData;
                    }
                }
                if (!cancelled) setBlobs(result);
            } catch {
                // silently hide tab on error
            }
            if (!cancelled) setLoading(false);
        }
        fetchAnalysisBlobs();
        return () => {
            cancelled = true;
        };
    }, [context.object?.id, api]);

    if (loading || (!blobs.normalized && !blobs.evalhook)) return <></>;

    return (
        <ObjectTab
            tab="normalized"
            icon={faCode}
            label="Analysis"
            actions={[
                ...(blobs.normalized
                    ? [
                          <ObjectAction
                              key="normalized"
                              label="Normalized blob"
                              link={`${remotePath}/blob/${blobs.normalized.id}`}
                          />,
                      ]
                    : []),
                ...(blobs.evalhook
                    ? [
                          <ObjectAction
                              key="evalhook"
                              label="Eval hook blob"
                              link={`${remotePath}/blob/${blobs.evalhook.id}`}
                          />,
                      ]
                    : []),
            ]}
            component={() => (
                <div>
                    {blobs.normalized && (
                        <div className="mb-4">
                            <h6>
                                Normalized PHP{" "}
                                <small className="text-muted">
                                    (
                                    <Link
                                        to={`${remotePath}/blob/${blobs.normalized.id}`}
                                    >
                                        TextBlob
                                    </Link>
                                    )
                                </small>
                            </h6>
                            <p className="text-muted small mb-2">
                                AST-based deobfuscation with variable resolution
                            </p>
                            <ObjectPreview
                                content={blobs.normalized.content}
                                mode="raw"
                                language="php"
                                showInvisibles
                            />
                        </div>
                    )}
                    {blobs.evalhook && (
                        <div className="mb-4">
                            <h6>
                                Eval Hook Output{" "}
                                <small className="text-muted">
                                    (
                                    <Link
                                        to={`${remotePath}/blob/${blobs.evalhook.id}`}
                                    >
                                        TextBlob
                                    </Link>
                                    )
                                </small>
                            </h6>
                            <p className="text-muted small mb-2">
                                Decoded payload captured by intercepting eval()
                                calls
                            </p>
                            <ObjectPreview
                                content={blobs.evalhook.content}
                                mode="raw"
                                language={
                                    blobs.evalhook.content
                                        .trimStart()
                                        .startsWith("<")
                                        ? "html"
                                        : "php"
                                }
                                showInvisibles
                            />
                        </div>
                    )}
                </div>
            )}
        />
    );
}
