from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class CineworldSession:
    session_token: str | None = None
    user_session_id: str | None = None
    member_token: str | None = None
    parent_session_token: str | None = None
    membership_id: str | None = None

    @property
    def authenticated(self) -> bool:
        return bool(self.session_token)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CineworldSession":
        return cls(
            session_token=data.get("session_token"),
            user_session_id=data.get("user_session_id"),
            member_token=data.get("member_token"),
            parent_session_token=data.get("parent_session_token"),
            membership_id=data.get("membership_id"),
        )


@dataclass(frozen=True, slots=True)
class SeatSelection:
    area_category_code: str
    area_number: int
    row_index: int
    column_index: int

    def to_api(self) -> dict[str, Any]:
        return {
            "AreaCategoryCode": self.area_category_code,
            "AreaNumber": self.area_number,
            "RowIndex": self.row_index,
            "ColumnIndex": self.column_index,
        }


@dataclass(frozen=True, slots=True)
class TicketSelection:
    ticket_type_code: str
    qty: int = 1
    third_party_member_card: str | None = None

    def to_api(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "TicketTypeCode": self.ticket_type_code,
            "Qty": self.qty,
        }
        if self.third_party_member_card:
            item["ThirdPartyMemberScheme"] = {
                "MemberCard": self.third_party_member_card,
            }
        return item
