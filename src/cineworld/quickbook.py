from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .constants import DEFAULT_LANGUAGE, DEFAULT_SITE_ID, WEBSITE_BASE
from .exceptions import CineworldHTTPError


class QuickbookAPI:
    def __init__(
        self,
        *,
        site_id: str = DEFAULT_SITE_ID,
        language: str = DEFAULT_LANGUAGE,
        timeout: float = 30.0,
    ):
        self.site_id = str(site_id)
        self.language = language
        self._client = httpx.Client(
            base_url=WEBSITE_BASE,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, *, lang: bool = True) -> Any:
        params = {"lang": self.language} if lang else None
        response = self._client.get(path, params=params)
        if not response.is_success:
            raise CineworldHTTPError(response.status_code, response.text[:500])
        try:
            return response.json()
        except ValueError:
            return response.text

    def all_feed_names(self) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/feed/{self.site_id}/allFeedNames",
            lang=False,
        )

    def feed(self, name: str) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/feed/{self.site_id}/byName/{quote(name, safe='-')}"
        )

    def film(self, distributor_code: str) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/{self.site_id}/films/byDistributorCode/"
            f"{quote(distributor_code, safe='-')}",
            lang=False,
        )

    def attributes(self) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/quickbook/{self.site_id}/attributes"
        )

    def cinemas_with_event_until(self, date: str) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/quickbook/{self.site_id}/cinemas/with-event/until/{quote(date)}"
        )

    def films_until(self, date: str) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/quickbook/{self.site_id}/films/until/{quote(date)}"
        )

    def groups_with_film_until(self, film_code: str, date: str) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/quickbook/{self.site_id}/groups/with-film/"
            f"{quote(film_code, safe='-')}/until/{quote(date)}"
        )

    def dates_in_group_with_film_until(
        self,
        group: str,
        film_code: str,
        date: str,
    ) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/quickbook/{self.site_id}/dates/in-group/"
            f"{quote(group, safe='-')}/with-film/{quote(film_code, safe='-')}/until/{quote(date)}"
        )

    def cinema_events(self, group: str, film_code: str, date: str) -> Any:
        return self._get(
            f"/uk/data-api-service/v1/quickbook/{self.site_id}/cinema-events/in-group/"
            f"{quote(group, safe='-')}/with-film/{quote(film_code, safe='-')}/at-date/{quote(date)}"
        )
