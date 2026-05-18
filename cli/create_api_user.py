"""Create a new Deckard ApiUser and issue an API key.

Usage:
    uv run python -m cli.create_api_user --name "acme-prod-integration"

An ApiUser is the calling server. It is standalone — Clients (the tenants
this caller submits requests for) are referenced per-request via
`client_identifier` in the request body, not at provisioning time.

The plaintext key is printed once and not persisted — only the SHA-256 hash
is stored.
"""

import argparse
import asyncio

from deckard.database.operations import api_user as api_user_ops
from deckard.database.session import get_sessionmaker


async def _run(name: str | None) -> int:
    async with get_sessionmaker()() as session:
        api_user, plaintext_key = await api_user_ops.create_with_api_key(session, name=name)
        await session.commit()

    print("ApiUser created.")
    print(f"  id:      {api_user.id}")
    print(f"  name:    {api_user.name or '-'}")
    print(f"  api key: {plaintext_key}")
    print()
    print("Store this key now — it will not be shown again.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cli.create_api_user",
        description="Create a new ApiUser (caller credential) and issue an API key.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Optional human-readable name for the ApiUser (e.g. 'acme-prod-integration').",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.name))


if __name__ == "__main__":
    raise SystemExit(main())
