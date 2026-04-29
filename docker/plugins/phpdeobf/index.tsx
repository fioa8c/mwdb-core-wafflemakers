import { faWandMagicSparkles } from "@fortawesome/free-solid-svg-icons";

import { ObjectTab } from "@mwdb-web/components/ShowObject";

import { PhpDeobfTab } from "./components/PhpDeobfTab";

export default () => ({
    sampleTabsAfter: [
        () => (
            <ObjectTab
                tab="phpdeobf"
                label="PHP Deobfuscate"
                icon={faWandMagicSparkles}
                component={PhpDeobfTab}
            />
        ),
    ],
});
