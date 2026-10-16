"""Databricks Apps put the authenticated caller in X-Forwarded-* headers."""
IDENTITY_HEADERS = ("x-forwarded-email", "x-forwarded-user", "x-forwarded-preferred-username")


def caller_identities(headers):
    return {headers[h] for h in IDENTITY_HEADERS if headers.get(h)}


def caller_name(headers):
    for h in IDENTITY_HEADERS:
        if headers.get(h):
            return headers[h]
    return "unknown"


def is_device(headers, device_principal):
    """True if any forwarded identity contains the device service principal's application ID."""
    return bool(device_principal) and any(device_principal in v for v in caller_identities(headers))
