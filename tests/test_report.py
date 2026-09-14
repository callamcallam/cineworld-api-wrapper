from datetime import date
from pathlib import Path

from cineworld.report import _env, _format, _save_env, _visits


def order(*, title="Film", film="F1", show="2026-09-10T19:00:00", refunded=False, attrs=None, session="S1"):
    attrs = attrs or ["2D"]
    return {
        "MyTransaction": {
            "BookingID": "ABC",
            "TheatreCode": "001",
            "Showtime": show,
            "HOFilmCode": film,
            "Refunded": refunded,
            "SessionId": session,
            "TransactionType": "Ticket",
        },
        "MyTheatre": {"TheatreCode": "001", "Name": "Example Cinema"},
        "MyMovie": {"FilmCode": film, "MasterMovieCode": film, "Title": title, "Attributes": attrs},
        "AttributeDescriptions": [{"acronym": a} for a in attrs],
    }


def test_formats():
    assert _format(order(attrs=["2D"])) == "Standard 2D"
    assert _format(order(attrs=["IMAX", "2D"])) == "IMAX"
    assert _format(order(attrs=["4DX", "3D"])) == "4DX 3D"


def test_visits_filter_refunds_future_and_dedupe():
    items = [
        order(),
        order(),
        order(refunded=True, session="S2"),
        order(show="2099-01-01T12:00:00", session="S3"),
    ]
    visits = _visits(items, 4.99, date(2026, 8, 1))
    assert len(visits) == 1
    assert visits[0].retail == 4.99


def test_env_roundtrip(tmp_path: Path):
    p = tmp_path / ".env"
    _save_env(p, {"CINEWORLD_EMAIL": "example@example.com", "CINEWORLD_PASSWORD": 'a#b"c'})
    loaded = _env(p)
    assert loaded["CINEWORLD_EMAIL"] == "example@example.com"
    assert loaded["CINEWORLD_PASSWORD"] == 'a#b"c'


def test_member_subscription_values_are_preferred(monkeypatch):
    from cineworld import report

    monkeypatch.setattr(report, "_official_prices", lambda: {1: 12.99, 2: 17.99, 3: 19.99, 4: 22.99})
    monkeypatch.setattr(report, "_student_discount", lambda: 25.0)
    member = {
        "subscriberMember": {
            "PaymentPastDue": False,
            "MemberSubscriptions": [{
                "SubscriptionType": "Unlimited",
                "Status": "Active",
                "PlanType": "Cineworld Unlimited Example (Grp2)",
                "SubscribeDate": "2026-07-30T00:00:00",
                "Cost": "17.99",
                "NextPaymentAmount": "13.49",
                "NextDueDate": "2026-09-27T00:00:00",
            }],
        }
    }
    orders = [{"MyTheatre": {"Name": "Example Cinema"}}]
    info = report._member_info(member, [], orders, {}, None, None)
    assert info["group"] == 2
    assert info["full"] == 17.99
    assert info["cost"] == 13.49
    assert info["start"] == date(2026, 7, 30)
    assert info["next_due"] == date(2026, 9, 27)
    assert info["source"] == "Cineworld account"
