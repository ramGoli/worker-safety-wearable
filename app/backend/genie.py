"""The console embeds the native Genie UI for the Worker Safety Assistant space (resources/genie.yml). It runs as
the signed-in supervisor, so the App only needs the space's id to build the iframe URL."""


def genie_urls(host, workspace_id, space_id):
    """The embeddable Genie page (workspace embedding must allow the App's domain) and the full one, as a fallback."""
    base, rooms = host.rstrip("/"), f"genie/rooms/{space_id}?o={workspace_id}"
    return {"embed_url": f"{base}/embed/{rooms}", "open_url": f"{base}/{rooms}"}
