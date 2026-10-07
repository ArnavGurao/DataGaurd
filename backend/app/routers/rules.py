"""Rule-set creation and listing (README 9.7). Rules are validated on write."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..errors import CODE_CONFLICT, ApiError
from ..models import RuleSet as RuleSetModel
from ..models import User
from ..schemas import RuleSetCreate, RuleSetOut
from ..services.rules import RuleSet, RuleSetError

router = APIRouter(prefix="/api/rule-sets", tags=["rule-sets"])


def _to_out(rule_set: RuleSetModel) -> RuleSetOut:
    return RuleSetOut(
        id=rule_set.id,
        name=rule_set.name,
        rules=rule_set.rules_json,
        created_at=rule_set.created_at,
    )


@router.post("", response_model=RuleSetOut, status_code=status.HTTP_201_CREATED)
def create_rule_set(
    payload: RuleSetCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RuleSetOut:
    try:
        rules = RuleSet.from_payload(payload.rules)
    except RuleSetError as exc:
        # The rule-set error code is meaningful to the UI, so surface it as-is
        # rather than collapsing everything into VALIDATION_ERROR.
        raise ApiError(422, exc.code, exc.message) from exc

    rule_set = RuleSetModel(
        id=uuid.uuid4(),
        owner_id=user.id,
        name=payload.name,
        # Store the normalized form so what runs is exactly what was validated.
        rules_json=rules.to_json(),
    )
    db.add(rule_set)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, CODE_CONFLICT, "You already have a rule set with that name.") from exc

    db.refresh(rule_set)
    return _to_out(rule_set)


@router.get("", response_model=list[RuleSetOut])
def list_rule_sets(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[RuleSetOut]:
    rule_sets = (
        db.execute(
            select(RuleSetModel)
            .where(RuleSetModel.owner_id == user.id)
            .order_by(RuleSetModel.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_out(rule_set) for rule_set in rule_sets]
