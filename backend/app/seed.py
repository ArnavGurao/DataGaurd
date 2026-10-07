"""Create a demo account and the sample rule set (README 9.7, 11.3).

Idempotent: re-running for an existing account reuses it and skips a rule set
that already exists, so it is safe in a demo rehearsal.

    .\\.venv\\Scripts\\python.exe -m app.seed --email demo@example.com --password demo-password
"""

from __future__ import annotations

import argparse
import logging
import uuid

from sqlalchemy import select

from .auth import hash_password, normalize_email
from .database import session_scope
from .models import RuleSet as RuleSetModel
from .models import User
from .services.rules import RuleSet

logger = logging.getLogger("dataguard.seed")

#: The example rule set from README 9.7, used by the dirty-CSV demo.
DEMO_RULE_SET_NAME = "Student Records"
DEMO_RULES = {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16, "max": 100, "integer": True}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]},
}


def seed(email: str, password: str, *, display_rules_name: str = DEMO_RULE_SET_NAME) -> str:
    email = normalize_email(email)
    # Validate through the same path the API uses, so the seed cannot create a
    # rule set the API would have rejected.
    rules = RuleSet.from_payload(DEMO_RULES)

    with session_scope() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            user = User(id=uuid.uuid4(), email=email, password_hash=hash_password(password))
            session.add(user)
            session.flush()
            logger.info("Created user %s.", email)
        else:
            logger.info("User %s already exists; reusing it.", email)

        existing = session.execute(
            select(RuleSetModel).where(
                RuleSetModel.owner_id == user.id, RuleSetModel.name == display_rules_name
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                RuleSetModel(
                    id=uuid.uuid4(),
                    owner_id=user.id,
                    name=display_rules_name,
                    rules_json=rules.to_json(),
                )
            )
            logger.info("Created rule set '%s'.", display_rules_name)
        else:
            logger.info("Rule set '%s' already exists; leaving it unchanged.", display_rules_name)

        return str(user.id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Seed the DataGuard demo account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--rule-set-name", default=DEMO_RULE_SET_NAME)
    args = parser.parse_args()

    user_id = seed(args.email, args.password, display_rules_name=args.rule_set_name)
    print(f"Seeded user {user_id}")


if __name__ == "__main__":
    main()
