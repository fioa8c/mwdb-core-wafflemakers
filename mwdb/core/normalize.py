import requests

from mwdb.core.log import getLogger

logger = getLogger()


def normalize_via_sandbox(sandbox_url, php_code):
    try:
        r = requests.post(
            f"{sandbox_url}/var_deobfuscate_web.php",
            data={"phpCode": php_code},
            timeout=60,
        )
        if r.ok:
            data = r.json()
            if data.get("success") and data.get("deobfuscated_code"):
                return data["deobfuscated_code"]
    except (requests.RequestException, ValueError):
        pass

    try:
        r = requests.post(
            f"{sandbox_url}/beautify.php",
            data={"phpCode": php_code},
            timeout=60,
        )
        if r.ok:
            data = r.json()
            if data.get("success") and data.get("beautified_code"):
                return data["beautified_code"]
    except (requests.RequestException, ValueError):
        pass

    return None


def analyze_via_sandbox(sandbox_url, php_code):
    try:
        r = requests.post(
            f"{sandbox_url}/analyze.php",
            data={"phpCode": php_code},
            timeout=60,
        )
        if r.ok and r.text.strip():
            return r.text.strip()
    except requests.RequestException:
        pass

    return None


def ensure_normalized_tlsh_definition():
    from mwdb.model import db
    from mwdb.model.attribute import AttributeDefinition

    existing = (
        db.session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == "normalized_tlsh")
        .first()
    )
    if existing:
        return
    defn = AttributeDefinition(
        key="normalized_tlsh",
        label="normalized_tlsh",
        description="TLSH hash of deobfuscated/normalized PHP code",
        url_template="",
        rich_template="",
        example_value="",
    )
    db.session.add(defn)
    db.session.commit()
    logger.info("Created attribute definition: normalized_tlsh")
