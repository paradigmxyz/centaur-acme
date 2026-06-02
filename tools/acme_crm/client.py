from __future__ import annotations

SAMPLE_ACCOUNTS = {
    "globex": {
        "name": "Globex",
        "owner": "Avery Chen",
        "plan": "Enterprise",
        "health": "green",
        "open_tickets": 1,
        "notes": "Pilot expanded from support automation to weekly reporting.",
    },
    "initech": {
        "name": "Initech",
        "owner": "Sam Rivera",
        "plan": "Business",
        "health": "yellow",
        "open_tickets": 3,
        "notes": "Waiting on SSO configuration before production launch.",
    },
}


def list_accounts() -> list[dict[str, object]]:
    return list(SAMPLE_ACCOUNTS.values())


def get_account(name: str) -> dict[str, object]:
    key = name.strip().lower()
    if key not in SAMPLE_ACCOUNTS:
        raise KeyError(f"unknown sample account: {name}")
    return SAMPLE_ACCOUNTS[key]


def health_summary() -> dict[str, object]:
    accounts = list_accounts()
    return {
        "account_count": len(accounts),
        "open_tickets": sum(int(account["open_tickets"]) for account in accounts),
        "yellow_accounts": [account["name"] for account in accounts if account["health"] == "yellow"],
        "sample_data": True,
    }


def support_playbook() -> dict[str, object]:
    return {
        "marker": "acme-live-overlay-2026-06-02",
        "priority_levels": ["low", "medium", "high"],
        "owner_action": "Send a concise account-owner next step after triage.",
        "sample_data": True,
    }
