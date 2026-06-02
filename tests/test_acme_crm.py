from tools.acme_crm.client import (
    get_account,
    health_summary,
    list_accounts,
    support_playbook,
)


def test_list_accounts_returns_sample_accounts():
    accounts = list_accounts()

    assert {account["name"] for account in accounts} == {"Globex", "Initech"}


def test_get_account_is_case_insensitive():
    assert get_account("GLOBEX")["owner"] == "Avery Chen"


def test_health_summary_marks_sample_data():
    summary = health_summary()

    assert summary["sample_data"] is True
    assert summary["open_tickets"] == 4


def test_support_playbook_has_live_overlay_marker():
    playbook = support_playbook()

    assert playbook["marker"] == "acme-live-overlay-2026-06-02"
