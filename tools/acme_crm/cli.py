from __future__ import annotations

import json

import click

from .client import get_account, health_summary, list_accounts, support_playbook


def emit(payload: object) -> None:
    click.echo(json.dumps(payload, indent=2, sort_keys=True))


@click.group()
def main() -> None:
    """Example ACME CRM tool."""


@main.command()
def accounts() -> None:
    """List sample accounts."""
    emit({"accounts": list_accounts(), "sample_data": True})


@main.command("account")
@click.argument("name")
def account(name: str) -> None:
    """Get one sample account by name."""
    try:
        emit({"account": get_account(name), "sample_data": True})
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("health")
def health() -> None:
    """Summarize sample account health."""
    emit(health_summary())


@main.command("playbook")
def playbook() -> None:
    """Return the current support playbook marker."""
    emit(support_playbook())


if __name__ == "__main__":
    main()
