"""Per-challenge / per-profile route isolation for grouped vBank images."""

COMMON_EXACT = {
    "/",
    "/robots.txt",
    "/owasp.pdf",
    "/customer/login",
    "/customer/logout",
    "/account/dashboard",
    "/staff-portal",
    "/staff-portal/logout",
    "/maintenance-portal",
    "/staff/dashboard",
}

COMMON_PREFIXES = (
    "/static/",
    "/images/",
    "/cryptojs/",
    "/wintools/",
)

# Prerequisite login/discovery plus the exploit surface for each key.
CHALLENGE_PREFIXES = {
    "sqli_login": (
        "/customer/login",
        "/account/dashboard",
    ),
    "open_redirect": (
        "/customer/login",
        "/account/dashboard",
    ),
    "idor_statements": (
        "/account/statements",
        "/api/v1/accounts/",
        "/api/statements/",
        "/banking/statements",
    ),
    "idor_transfer": (
        "/account/transfer",
        "/account/statements",
        "/api/v1/accounts/",
        "/banking/transfer",
        "/banking/statements",
    ),
    "race_condition": (
        "/account/express-transfer",
        "/api/v1/transfer/express",
        "/api/v1/account/status",
        "/api/v1/account/reset",
        "/api/instant-transfer",
        "/api/account/status",
        "/api/account/reset",
        "/banking/express-transfer",
        "/instant-transfer",
    ),
    "reflected_xss": (
        "/search",
    ),
    "xss": (
        "/account/support",
        "/staff/tickets",
        "/feedback",
        "/staff/reviews",
        "/internal/staff/tickets",
        "/support/ticket",
    ),
    "staff_sqli": (
        "/maintenance-portal",
        "/staff-portal",
        "/staff/dashboard",
    ),
    "jwt": (
        "/staff/api-docs",
        "/api/v2/auth/token",
        "/api/v2/corporate/vault",
        "/api/v2/profile",
        "/api/v2/token",
        "/api/v2/admin/vault",
        "/developer/api-docs",
        "/api-portal.php",
    ),
    "mass_assignment": (
        "/api/v1/staff/onboard",
        "/staff/dashboard",
    ),
    "rce": (
        "/staff/maintenance",
        "/api/internal/maintenance",
        "/w-shell.php",
    ),
    "path_traversal": (
        "/staff/files",
    ),
    "crypto_ecb": (
        "/account/receipts",
        "/api/v1/receipts/",
        "/api/receipt/",
        "/banking/receipts",
        "/receipts",
    ),
    "ssrf": (
        "/staff/support-tools",
        "/support/document-fetch",
        "/api/internal/debug",
        "/support/fetch-url",
        "/internal/debug",
        "/support/help",
    ),
    "xxe": (
        "/staff/payroll",
        "/import/transactions",
        "/hr/payroll/import",
    ),
    "proto_pollution": (
        "/staff/settings",
        "/api/settings/",
        "/settings-page",
        "/account/settings",
    ),
    "ssti": (
        "/account/profile",
    ),
}

PROFILE_KEYS = {
    "auth": ("sqli_login", "open_redirect"),
    "idor": ("idor_statements", "idor_transfer"),
    "logic": ("race_condition",),
    "xss": ("reflected_xss", "xss"),
    "staff-auth": ("staff_sqli", "jwt", "mass_assignment"),
    "staff-deep": ("rce", "path_traversal"),
    "crypto": ("crypto_ecb",),
    "integration": ("ssrf", "xxe"),
    "analytics-gateway": ("proto_pollution",),
    "template": ("ssti",),
}

OPEN_PROFILES = {"", "all", "vbank-ctf"}


def _matches(path, prefix):
    if path == prefix:
        return True
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path.startswith(prefix + "/") or path.startswith(prefix + "?")


def allowed_prefixes(challenge_key="", profile="all"):
    key = (challenge_key or "").strip()
    if key and key in CHALLENGE_PREFIXES:
        return CHALLENGE_PREFIXES[key]
    prof = (profile or "all").strip()
    if prof in OPEN_PROFILES:
        return None
    keys = PROFILE_KEYS.get(prof, ())
    prefixes = []
    for k in keys:
        prefixes.extend(CHALLENGE_PREFIXES.get(k, ()))
    return tuple(prefixes)


def path_allowed(path, challenge_key="", profile="all"):
    path = (path or "/").split("?", 1)[0]
    if path in COMMON_EXACT:
        return True
    for prefix in COMMON_PREFIXES:
        if path.startswith(prefix):
            return True
    prefixes = allowed_prefixes(challenge_key, profile)
    if prefixes is None:
        return True
    return any(_matches(path, prefix) for prefix in prefixes)
