import { faMagnifyingGlassChart } from "@fortawesome/free-solid-svg-icons";

import { ObjectTab } from "@mwdb-web/components/ShowObject";

import { YaraXRegexTab } from "./components/YaraXRegexTab";

export default () => ({
    sampleTabsAfter: [
        () => (
            <ObjectTab
                tab="yarax-regex"
                label="YARA-X Regex"
                icon={faMagnifyingGlassChart}
                component={YaraXRegexTab}
            />
        ),
    ],
});
