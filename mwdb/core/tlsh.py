import tlsh

from mwdb.core.log import getLogger

logger = getLogger()


def calc_tlsh(stream) -> str | None:
    """
    Compute TLSH hash of a file stream. Returns None if TLSH cannot produce
    a valid hash (e.g. file smaller than the ~50-byte floor or insufficient
    byte diversity).
    """
    stream.seek(0)
    hasher = tlsh.Tlsh()
    while chunk := stream.read(1024 * 256):
        hasher.update(chunk)
    try:
        hasher.final()
        digest = hasher.hexdigest()
    except Exception:
        logger.debug("TLSH computation failed for stream", exc_info=True)
        return None
    if not digest or digest == "TNULL":
        return None
    return digest
