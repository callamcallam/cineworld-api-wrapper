from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

from playwright.sync_api import Response

from .browser import BrowserSession
from .constants import EXPERIENCE_BASE
from .exceptions import (
    AuthenticationError,
    CineworldHTTPError,
    LoginTimeoutError,
    NotAuthenticatedError,
)
from .models import CineworldSession, SeatSelection, TicketSelection
from .quickbook import QuickbookAPI
from .storage import SessionStore


class Cineworld:
    """Browser-assisted Cineworld UK API wrapper."""

    def __init__(
        self,
        data_dir: str | Path = ".cineworld",
        *,
        headless: bool = False,
        browser_channel: str | None = "auto",
        browser_mode: str = "native",
        site_id: str = "10108",
        language: str = "en_GB",
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.store = SessionStore(self.data_dir / "session.json")
        self.session = self.store.load()
        profile_name = "profile-native" if browser_mode == "native" else "profile"
        self.browser = BrowserSession(
            self.data_dir / profile_name,
            headless=headless,
            browser_channel=browser_channel,
            mode=browser_mode,
        )
        self.quickbook = QuickbookAPI(site_id=site_id, language=language)

        self._last_login_error: str | None = None
        self._last_login_payload: dict[str, Any] | None = None

    def start(self) -> None:
        self.browser.start(self._on_response)

    def close(self) -> None:
        self.browser.close()
        self.quickbook.close()

    def __enter__(self) -> "Cineworld":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def login(
        self,
        email: str | None = None,
        password: str | None = None,
        *,
        auto_submit: bool = False,
        timeout: float = 300.0,
    ) -> CineworldSession:
        """Open Cineworld's real login page and wait for a successful login."""
        self.start()
        page = self.browser.open_login()
        self._last_login_error = None
        self._last_login_payload = None

        existing = self._try_member_from_browser()
        if existing and isinstance(existing, dict) and existing.get("SessionToken"):
            self._update_session(existing)
            return self.session

        if email is not None or password is not None:
            if not email or not password:
                raise ValueError("Both email and password must be supplied together")

            if not self.browser.fill_login(email, password):
                print(
                    "Could not confidently find Cineworld's login fields. "
                    "Enter the credentials manually in the opened browser."
                )
            elif auto_submit:
                if not self.browser.submit_login_form():
                    print(
                        "Credentials were filled, but the wrapper could not safely "
                        "identify the form submit button. Click Log in manually."
                    )
            else:
                print("Credentials filled. Click Log in in the browser when ready.")
        else:
            print("Log in to Cineworld in the opened browser.")

        deadline = time.monotonic() + timeout
        shown_error: str | None = None

        while time.monotonic() < deadline:
            if self.session.authenticated:
                self.store.save(self.session)
                return self.session

            if self._last_login_error and self._last_login_error != shown_error:
                print(f"Cineworld login response: {self._last_login_error}")
                shown_error = self._last_login_error

            if "/login" not in page.url:
                existing = self._try_member_from_browser()
                if existing and isinstance(existing, dict) and existing.get("SessionToken"):
                    self._update_session(existing)
                    return self.session

            page.wait_for_timeout(500)

        detail = self._last_login_error or "login was not completed"
        raise LoginTimeoutError(f"Cineworld login timed out: {detail}")

    def logout_local(self) -> None:
        self.session = CineworldSession()
        self.store.save(self.session)

    def _on_response(self, response: Response) -> None:
        try:
            if not response.url.startswith(EXPERIENCE_BASE + "/api/"):
                return
            path = response.url.split("?", 1)[0]
            if path not in {
                EXPERIENCE_BASE + "/api/login",
                EXPERIENCE_BASE + "/api/Member",
                EXPERIENCE_BASE + "/api/CreateVistaSession",
            }:
                return

            if path.endswith("/api/login") and response.status == 403:
                try:
                    mitigated = response.header_value("cf-mitigated")
                except Exception:
                    mitigated = None
                if mitigated == "challenge":
                    self._last_login_error = (
                        "Cloudflare challenged the login request before Cineworld "
                        "checked the credentials. Complete the visible Turnstile/"
                        "browser challenge manually and retry Log in."
                    )
                    return

            try:
                data = response.json()
            except Exception:
                if path.endswith("/api/login") and response.status >= 400:
                    self._last_login_error = (
                        f"Login request was blocked before Cineworld authentication "
                        f"(HTTP {response.status})."
                    )
                return

            if not isinstance(data, dict):
                return

            if path.endswith("/api/login"):
                self._last_login_payload = data
                if data.get("errorCode") or (
                    data.get("statusCode") and int(data.get("statusCode")) >= 400
                ):
                    self._last_login_error = (
                        data.get("userMessage")
                        or data.get("message")
                        or "Cineworld rejected the login"
                    )
                    return
                self._last_login_error = None

            self._update_session(data)
        except Exception:
            return

    def _update_session(self, data: dict[str, Any]) -> None:
        changed = False
        mapping = {
            "SessionToken": "session_token",
            "UserSessionId": "user_session_id",
            "userSessionId": "user_session_id",
            "MemberToken": "member_token",
            "ParentSessionToken": "parent_session_token",
            "MembershipId": "membership_id",
        }
        for source, target in mapping.items():
            value = data.get(source)
            if value and getattr(self.session, target) != str(value):
                setattr(self.session, target, str(value))
                changed = True
        if changed:
            self.store.save(self.session)

    def _try_member_from_browser(self) -> Any | None:
        try:
            return self._browser_fetch("GET", "/api/Member")
        except Exception:
            return None

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> Any:
        return self._browser_fetch(method, endpoint, params=params, json_body=json_body)

    def _browser_fetch(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> Any:
        self.start()
        page = self.browser.page
        assert page is not None

        if not page.url.startswith(EXPERIENCE_BASE):
            page.goto(EXPERIENCE_BASE, wait_until="domcontentloaded", timeout=60_000)

        url = endpoint if endpoint.startswith("http") else urljoin(EXPERIENCE_BASE, endpoint)
        result = page.evaluate(
            """
            async ({url, method, params, body}) => {
                const target = new URL(url);
                if (params) {
                    for (const [key, value] of Object.entries(params)) {
                        if (value !== null && value !== undefined) {
                            target.searchParams.set(key, String(value));
                        }
                    }
                }

                const options = {
                    method: method,
                    credentials: "include",
                    headers: {"Accept": "application/json, text/plain, */*"}
                };

                if (body !== null && body !== undefined) {
                    options.headers["Content-Type"] = "application/json";
                    options.body = JSON.stringify(body);
                }

                const response = await fetch(target.toString(), options);
                const text = await response.text();
                let data;
                try { data = JSON.parse(text); } catch { data = text; }

                return {
                    ok: response.ok,
                    status: response.status,
                    url: response.url,
                    data
                };
            }
            """,
            {"url": url, "method": method.upper(), "params": params, "body": json_body},
        )

        data = result["data"]
        if not result["ok"]:
            raise CineworldHTTPError(result["status"], str(data)[:500], data)
        if isinstance(data, dict) and (
            data.get("errorCode")
            or (data.get("statusCode") and int(data.get("statusCode")) >= 400)
        ):
            message = data.get("userMessage") or data.get("message") or str(data)
            if int(data.get("statusCode") or 0) in {401, 403}:
                raise AuthenticationError(message)
            raise CineworldHTTPError(result["status"], message, data)

        if isinstance(data, dict):
            self._update_session(data)
        return data

    def captcha_enabled(self) -> bool:
        data = self.request("GET", "/api/CaptchaEnabled")
        return bool(data.get("enabled")) if isinstance(data, dict) else False

    def get_member(self) -> Any:
        return self.request("GET", "/api/Member")

    def get_recognitions(self) -> Any:
        return self.request(
            "GET", "/api/Recognitions", params={"sessionToken": self._session_token()}
        )

    def get_orders(self) -> Any:
        return self.request(
            "GET", "/api/orders", params={"sessionToken": self._session_token()}
        )

    def get_transactions(self) -> Any:
        return self.request(
            "GET", "/api/transactions", params={"sessionToken": self._session_token()}
        )

    def get_order_by_id(self, theatre_code: str, order_id: str) -> Any:
        return self.request(
            "GET",
            "/api/OrderById",
            params={
                "theatreCode": theatre_code,
                "orderId": order_id,
                "sessionToken": self._session_token(),
            },
        )

    def get_payment_methods(self) -> Any:
        return self.request(
            "GET", "/api/paymentMethods", params={"sessionToken": self._session_token()}
        )

    def authorize_profile_token(self, *, subscription: bool | None = None) -> Any:
        params: dict[str, Any] = {"sessionToken": self._session_token()}
        if subscription is not None:
            params["subscription"] = str(subscription).lower()
        return self.request("GET", "/api/authorizeProfileToken", params=params)

    def get_theatres(self) -> Any:
        return self.request("GET", "/api/theatres")

    def get_member_optins(self) -> Any:
        return self.request("GET", "/api/memberOptin")

    def update_member_optins(self, optins: list[dict[str, Any]]) -> Any:
        return self.request("PUT", "/api/memberOptin", json_body=optins)

    def refresh_member(self) -> Any:
        if not self.session.membership_id:
            raise NotAuthenticatedError("Missing MembershipId")
        return self.request(
            "PUT",
            "/api/Member",
            json_body={
                "MembershipId": self.session.membership_id,
                "SessionToken": self._session_token(),
            },
        )

    def get_order_media(self, theatre_code: str, session_id: str) -> Any:
        return self.request(
            "GET",
            "/api/OrderMedia",
            params={"theatreCode": theatre_code, "sessionId": session_id},
        )

    def create_vista_session(self) -> Any:
        return self.request(
            "POST",
            "/api/CreateVistaSession",
            json_body={
                "credential1": self._session_token(),
                "credential2": "WebBookingLogin",
                "isWebBookingLogin": True,
            },
        )

    def get_tickets_for_session(
        self,
        *,
        theatre_code: str,
        vista_session: str,
        date: str,
        cart_id: str,
    ) -> Any:
        return self.request(
            "GET",
            "/api/GetTicketsForSession",
            params={
                "theatreCode": theatre_code,
                "vistaSession": vista_session,
                "date": date,
                "cartId": cart_id,
                "sessionToken": self._session_token(),
            },
        )

    def get_order(self, user_session_id: str | None = None) -> Any:
        return self.request(
            "POST",
            "/api/GetOrder",
            json_body={"UserSessionId": user_session_id or self._user_session_id()},
        )

    def get_cart(self, cart_id: str, user_session_id: str | None = None) -> Any:
        return self.request(
            "POST",
            "/api/cart",
            params={"cartId": cart_id},
            json_body={"UserSessionId": user_session_id or self._user_session_id()},
        )

    def add_tickets(
        self,
        *,
        cinema_id: str,
        session_id: str,
        tickets: Iterable[TicketSelection | dict[str, Any]],
        captcha: str,
        user_session_id: str | None = None,
        user_selected_seating_supported: bool = True,
    ) -> Any:
        prepared = [t.to_api() if isinstance(t, TicketSelection) else dict(t) for t in tickets]
        return self.request(
            "POST",
            "/api/AddTickets",
            json_body={
                "UserSessionId": user_session_id or self._user_session_id(),
                "CinemaId": cinema_id,
                "SessionId": session_id,
                "TicketTypes": prepared,
                "UserSelectedSeatingSupported": user_selected_seating_supported,
                "ReturnDiscountInfo": True,
                "ReturnOrder": True,
                "captcha": captcha,
            },
        )

    def get_seat_plan(
        self,
        *,
        theatre_code: str,
        vista_session: str,
        cinema_id: str,
        session_id: str,
        user_session_id: str | None = None,
    ) -> Any:
        return self.request(
            "POST",
            "/api/SeatPlan",
            params={"theatreCode": theatre_code, "vistaSession": vista_session},
            json_body={
                "CinemaId": cinema_id,
                "SessionId": session_id,
                "UserSessionId": user_session_id or self._user_session_id(),
                "IncludeBrokenSeats": True,
                "IncludeHouseSpecialSeats": True,
                "IncludeGreyAndSofaSeats": True,
                "IncludeAllSeatPriorities": True,
                "IncludeSeatNumbers": True,
                "IncludeCompanionSeats": True,
            },
        )

    def set_seats(
        self,
        *,
        cinema_id: str,
        session_id: str,
        seats: Iterable[SeatSelection | dict[str, Any]],
        user_session_id: str | None = None,
    ) -> Any:
        prepared = [s.to_api() if isinstance(s, SeatSelection) else dict(s) for s in seats]
        return self.request(
            "POST",
            "/api/SetSeats",
            json_body={
                "CinemaId": cinema_id,
                "SessionId": session_id,
                "UserSessionId": user_session_id or self._user_session_id(),
                "ReturnOrder": True,
                "SelectedSeats": prepared,
            },
        )

    def get_concessions(self, *, theatre_code: str, cart_id: str) -> Any:
        return self.request(
            "GET",
            "/api/Concessions",
            params={"theatreCode": theatre_code, "cartId": cart_id},
        )

    def browser_user_agent(self) -> str:
        self.start()
        return self.browser.user_agent()

    def cookies(self) -> list[dict[str, Any]]:
        self.start()
        assert self.browser.context is not None
        return self.browser.context.cookies()

    def _session_token(self) -> str:
        if not self.session.session_token:
            raise NotAuthenticatedError("Not logged in. Call cineworld.login(...) first.")
        return self.session.session_token

    def _user_session_id(self) -> str:
        if not self.session.user_session_id:
            raise NotAuthenticatedError(
                "No booking UserSessionId yet. Call create_vista_session() first."
            )
        return self.session.user_session_id
