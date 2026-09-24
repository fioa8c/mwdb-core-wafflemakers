import { APIContext } from "@mwdb-web/commons/api";
import { useCallback, useContext, useEffect, useState } from "react";
import { ObjectContext, useTabContext } from "./ShowObject";
import { negateBuffer, humanFileSize } from "@mwdb-web/commons/helpers";
import { ObjectPreview } from "@mwdb-web/commons/ui";
import { ObjectData } from "@mwdb-web/types/types";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";

const PHP_EXTENSIONS = [".php", ".phtml", ".php5", ".php7", ".inc"];

function detectLanguage(fileName: string | undefined): string | undefined {
    if (!fileName) return undefined;
    const lower = fileName.toLowerCase();
    if (PHP_EXTENSIONS.some((ext) => lower.endsWith(ext))) return "php";
    if (lower.endsWith(".html") || lower.endsWith(".htm")) return "html";
    if (lower.endsWith(".js")) return "json";
    return undefined;
}

const CONTENT_LENGTH_LIMIT = 20 * 1048576; // 20 MiB

export function SamplePreview() {
    const [content, setContent] = useState<ArrayBuffer>(new ArrayBuffer(0));
    const [fullContentLength, setFullContentLength] = useState<number>(0);
    const [loadAll, setLoadAll] = useState<boolean>(false);
    const api = useContext(APIContext);
    const objectContext = useContext(ObjectContext);
    const tabContext = useTabContext();

    const object = objectContext?.object as ObjectData | undefined;
    const language = detectLanguage(object?.file_name);

    const updateSample = useCallback(async () => {
        try {
            const fileId = objectContext!.object!.id!;
            const obfuscate = 1;
            const fileContentResponse = await api.downloadFile(
                fileId,
                obfuscate,
                loadAll ? "" : `bytes=0-${CONTENT_LENGTH_LIMIT - 1}`
            );
            console.log(fileContentResponse.headers);
            const fullContentLength =
                fileContentResponse.headers["content-range"]?.split("/")[1];
            console.log(fullContentLength);
            if (fullContentLength) {
                console.log(fullContentLength);
                setFullContentLength(parseInt(fullContentLength));
            }
            const fileContentResponseData = negateBuffer(
                fileContentResponse.data
            );
            setContent(fileContentResponseData);
        } catch (e) {
            objectContext.setObjectError(e);
        }
    }, [objectContext?.object?.id, loadAll]);

    useEffect(() => {
        updateSample();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [updateSample]);

    return (
        <>
            {!loadAll && fullContentLength > content.byteLength ? (
                <div className="alert alert-warning" role="alert">
                    <FontAwesomeIcon
                        className="ml-1 mt-1"
                        icon={faTriangleExclamation}
                        size="1x"
                        pull="left"
                    />
                    <span className="ml-1">
                        File size is {humanFileSize(fullContentLength)}. Only
                        the first {humanFileSize(CONTENT_LENGTH_LIMIT)} is
                        currently displayed.{" "}
                        <button
                            className="unstyled-btn btn-link"
                            style={{ textDecoration: "underline" }}
                            onClick={() => setLoadAll(true)}
                        >
                            (Load the full content anyway?)
                        </button>
                    </span>
                </div>
            ) : (
                []
            )}
            <ObjectPreview
                content={content}
                mode={tabContext.subTab || "raw"}
                language={language}
                showInvisibles
            />
        </>
    );
}
