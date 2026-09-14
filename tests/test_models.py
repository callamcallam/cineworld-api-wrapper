from cineworld.models import CineworldSession, SeatSelection, TicketSelection


def test_session_roundtrip():
    original = CineworldSession(session_token="abc", user_session_id="def")
    restored = CineworldSession.from_dict(original.to_dict())
    assert restored == original
    assert restored.authenticated is True


def test_seat_api_shape():
    seat = SeatSelection("0000000007", 1, 7, 21)
    assert seat.to_api() == {
        "AreaCategoryCode": "0000000007",
        "AreaNumber": 1,
        "RowIndex": 7,
        "ColumnIndex": 21,
    }


def test_ticket_api_shape():
    ticket = TicketSelection("0760", qty=1, third_party_member_card="MEMBER")
    assert ticket.to_api()["ThirdPartyMemberScheme"]["MemberCard"] == "MEMBER"
