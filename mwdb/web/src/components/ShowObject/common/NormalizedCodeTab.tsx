import { useContext, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { faCode } from "@fortawesome/free-solid-svg-icons";
import { APIContext } from "@mwdb-web/commons/api";
import { ObjectContext } from "@mwdb-web/commons/context";
import { ObjectAction, ObjectTab, ObjectPreview } from "@mwdb-web/commons/ui";
import { useRemotePath } from "@mwdb-web/commons/remotes";
import { BlobData } from "@mwdb-web/types/types";

export function NormalizedCodeTab() {
    const api = useContext(APIContext);
    const context = useContext(ObjectContext);
    const remotePath = useRemotePath();
    const [blob, setBlob] = useState<BlobData | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        async function fetchNormalizedBlob() {
            if (!context.object?.id) return;
            try {
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                const relationsResp = await api.getObjectRelations(
                    context.object.id as any
                );
                const children = relationsResp.data.children || [];
                const blobChild = children.find(
                    (c: { type: string }) => c.type === "text_blob"
                );
                if (!blobChild) {
                    if (!cancelled) setLoading(false);
                    return;
                }
                const blobResp = await api.getObject("blob", blobChild.id);
                const blobData = blobResp.data as BlobData;
                if (blobData.blob_type === "normalized-php" && !cancelled) {
                    setBlob(blobData);
                }
            } catch {
                // Relations or blob fetch failed — silently hide the tab
            }
            if (!cancelled) setLoading(false);
        }
        fetchNormalizedBlob();
        return () => {
            cancelled = true;
        };
    }, [context.object?.id, api]);

    if (loading || !blob) return <></>;

    return (
        <ObjectTab
            tab="normalized"
            icon={faCode}
            label="Normalized PHP"
            actions={[
                <ObjectAction
                    label="Go to blob"
                    link={`${remotePath}/blob/${blob.id}`}
                />,
            ]}
            component={() => (
                <div>
                    <div className="mb-2">
                        <small className="text-muted">
                            Deobfuscated/normalized version of this file (stored
                            as{" "}
                            <Link to={`${remotePath}/blob/${blob.id}`}>
                                TextBlob
                            </Link>
                            )
                        </small>
                    </div>
                    <ObjectPreview
                        content={blob.content}
                        mode="raw"
                        showInvisibles
                    />
                </div>
            )}
        />
    );
}
