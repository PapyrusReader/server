"""Operator commands for Papyrus account roles."""

import argparse
import asyncio
from uuid import UUID

from papyrus.core.database import async_session_maker
from papyrus.core.exceptions import NotFoundError
from papyrus.schemas.user import AccountRole
from papyrus.services.admin import set_role


async def apply_role(user_id: UUID, role: AccountRole) -> None:
    async with async_session_maker() as session:
        await set_role(session, user_id, role)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    assign = commands.add_parser("set-role", help="Grant or revoke admin access")
    assign.add_argument("user_id", type=UUID)
    assign.add_argument("role", choices=("user", "admin"))
    args = parser.parse_args()

    try:
        asyncio.run(apply_role(args.user_id, args.role))
    except NotFoundError:
        parser.exit(1, "User not found\n")

    print(f"Account {args.user_id} now has role {args.role}.")
