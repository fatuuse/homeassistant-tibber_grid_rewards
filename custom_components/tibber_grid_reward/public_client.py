import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

PUBLIC_API_URL = "https://api.tibber.com/v1-beta/gql"


class TibberPublicException(Exception):
    """Base exception for the Tibber public API client."""


class TibberPublicAuthError(TibberPublicException):
    """Exception for authentication errors."""


def _safe_dict(value: Any) -> dict[str, Any]:
    """Return value if it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


class TibberPublicAPI:
    """A client for the public Tibber API."""

    def __init__(self, token: str, client: httpx.AsyncClient):
        """Initialize the client."""
        self._token = token
        self._client = client

        self.headers = {
            "Authorization": f"Bearer {token}",
        }

        self._price_cache: dict[str, dict[str, Any] | None] = {}
        self._price_cache_time: dict[str, datetime] = {}

    def _check_graphql_errors(self, data: dict[str, Any]) -> None:
        """Raise exception if GraphQL returned errors."""

        errors = data.get("errors")

        if errors:
            raise TibberPublicException(
                f"GraphQL returned errors: {errors}"
            )

    async def get_homes(self) -> list[dict[str, Any]]:
        """Fetch Tibber homes."""

        _LOGGER.debug("Fetching Tibber homes from public API.")

        query = """
        {
          viewer {
            homes {
              id
              appNickname
              address {
                address1
              }
            }
          }
        }
        """

        try:
            response = await self._client.post(
                PUBLIC_API_URL,
                headers=self.headers,
                json={"query": query},
            )

            response.raise_for_status()

            data = response.json()

            self._check_graphql_errors(data)

            viewer = _safe_dict(
                _safe_dict(data.get("data")).get("viewer")
            )

            homes = viewer.get("homes") or []

            for home in homes:
                if not isinstance(home, dict):
                    continue

                address = _safe_dict(home.get("address"))

                home["title"] = (
                    home.get("appNickname")
                    or address.get("address1")
                    or home.get("id", "Unknown home")
                )

            _LOGGER.debug(
                "Fetched %s homes from public API",
                len(homes),
            )

            return homes

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                _LOGGER.error("Authentication failed with public API")
                raise TibberPublicAuthError from e

            _LOGGER.error(
                "Could not fetch homes from public API: %s",
                e,
            )

            raise TibberPublicException from e

        except Exception as e:
            _LOGGER.error(
                "An unexpected error occurred while fetching homes: %s",
                e,
            )

            raise TibberPublicException from e

    async def get_all_homes_price_info(self) -> dict[str, dict[str, Any]]:
        """Fetch price information for all homes."""

        _LOGGER.debug("Fetching price information for all homes")

        now = datetime.now(UTC)

        query = """
        query {
          viewer {
            homes {
              id
              currentSubscription {
                priceInfo {
                  today {
                    total
                    energy
                    tax
                    startsAt
                    currency
                  }
                  tomorrow {
                    total
                    energy
                    tax
                    startsAt
                    currency
                  }
                }
              }
            }
          }
        }
        """

        try:
            response = await self._client.post(
                PUBLIC_API_URL,
                headers=self.headers,
                json={"query": query},
            )

            response.raise_for_status()

            data = response.json()

            self._check_graphql_errors(data)

            viewer = _safe_dict(
                _safe_dict(data.get("data")).get("viewer")
            )

            homes = viewer.get("homes") or []

            if homes is None:
                homes = []

            results: dict[str, dict[str, Any]] = {}

            for home in homes:
                if not isinstance(home, dict):
                    continue

                home_id = home.get("id")

                if not home_id:
                    continue

                subscription = _safe_dict(
                    home.get("currentSubscription")
                )

                price_info = subscription.get("priceInfo")

                if price_info is not None:
                    self._price_cache[home_id] = price_info
                    self._price_cache_time[home_id] = now
                    results[home_id] = price_info
                else:
                    _LOGGER.debug(
                        "No price information returned for home %s",
                        home_id,
                    )

            return results

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                _LOGGER.error("Authentication failed with public API")
                raise TibberPublicAuthError from e

            _LOGGER.error(
                "Could not fetch all price info from public API: %s",
                e,
            )

            return {}

        except Exception as e:
            _LOGGER.error(
                "An unexpected error occurred while fetching all price info: %s",
                e,
            )

            return {}

    async def get_price_info(
        self,
        home_id: str,
    ) -> dict[str, Any] | None:
        """Fetch price information for a specific home."""

        now = datetime.now(UTC)

        cache_time = self._price_cache_time.get(home_id)

        if (
            cache_time
            and home_id in self._price_cache
            and now - cache_time < timedelta(hours=6)
        ):
            _LOGGER.debug(
                "Returning cached price info for home %s",
                home_id,
            )

            return self._price_cache[home_id]

        await self.get_all_homes_price_info()

        if home_id in self._price_cache:
            return self._price_cache[home_id]

        _LOGGER.debug(
            "Price info not found in cache, fetching home %s directly",
            home_id,
        )

        query = """
        query($homeId: ID!) {
          viewer {
            home(id: $homeId) {
              currentSubscription {
                priceInfo {
                  today {
                    total
                    energy
                    tax
                    startsAt
                    currency
                  }
                  tomorrow {
                    total
                    energy
                    tax
                    startsAt
                    currency
                  }
                }
              }
            }
          }
        }
        """

        payload = {
            "query": query,
            "variables": {
                "homeId": home_id,
            },
        }

        try:
            response = await self._client.post(
                PUBLIC_API_URL,
                headers=self.headers,
                json=payload,
            )

            response.raise_for_status()

            data = response.json()

            self._check_graphql_errors(data)

            viewer = _safe_dict(
                _safe_dict(data.get("data")).get("viewer")
            )

            home = _safe_dict(
                viewer.get("home")
            )

            subscription = _safe_dict(
                home.get("currentSubscription")
            )

            price_info = subscription.get("priceInfo")

            if not home:
                _LOGGER.warning(
                    "Tibber returned no home for id %s",
                    home_id,
                )

                return None

            if not subscription:
                _LOGGER.warning(
                    "No current subscription for home %s",
                    home_id,
                )

                return None

            if price_info is not None:
                self._price_cache[home_id] = price_info
                self._price_cache_time[home_id] = now

            return price_info

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                _LOGGER.error(
                    "Authentication failed with public API"
                )
                raise TibberPublicAuthError from e

            _LOGGER.error(
                "Could not fetch price info from public API: %s",
                e,
            )

            return None

        except Exception as e:
            _LOGGER.error(
                "An unexpected error occurred while fetching price info: %s",
                e,
            )

            return None
