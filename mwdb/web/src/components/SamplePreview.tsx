import { APIContext } from "@mwdb-web/commons/api";
import { useContext, useEffect, useState } from "react";
import { ObjectContext, useTabContext } from "./ShowObject";
import { negateBuffer } from "@mwdb-web/commons/helpers";
import { ObjectPreview } from "@mwdb-web/commons/ui";
import { ObjectData } from "@mwdb-web/types/types";

const PHP_EXTENSIONS = [".php", ".phtml", ".php5", ".php7", ".inc"];

function detectLanguage(fileName: string | undefined): string | undefined {
    if (!fileName) return undefined;
    const lower = fileName.toLowerCase();
    if (PHP_EXTENSIONS.some((ext) => lower.endsWith(ext))) return "php";
    if (lower.endsWith(".html") || lower.endsWith(".htm")) return "html";
    if (lower.endsWith(".js")) return "json";
    return undefined;
}

export function SamplePreview() {
    const [content, setContent] = useState<ArrayBuffer>(new ArrayBuffer(0));
    const api = useContext(APIContext);
    const objectContext = useContext(ObjectContext);
    const tabContext = useTabContext();

    const object = objectContext?.object as ObjectData | undefined;
    const language = detectLanguage(object?.file_name);

    async function updateSample() {
        try {
            const fileId = objectContext!.object!.id!;
            const obfuscate = 1;
            const fileContentResponse = await api.downloadFile(
                fileId,
                obfuscate
            );
            const fileContentResponseData = negateBuffer(
                fileContentResponse.data
            );
            setContent(fileContentResponseData);
        } catch (e) {
            objectContext.setObjectError(e);
        }
    }

    useEffect(() => {
        updateSample();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [objectContext?.object?.id]);

    return (
        <ObjectPreview
            content={content}
            mode={tabContext.subTab || "raw"}
            language={language}
            showInvisibles
        />
    );
}
