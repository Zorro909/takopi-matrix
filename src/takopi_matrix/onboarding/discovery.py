"""Homeserver discovery and credential testing."""

from __future__ import annotations

import httpx
import nio


async def _discover_homeserver(server_name: str) -> str:
    """
    Discover homeserver URL using .well-known.

    Tries https://{server_name}/.well-known/matrix/client first,
    falls back to https://{server_name} if discovery fails.
    """
    if server_name.startswith("http://") or server_name.startswith("https://"):
        return server_name.rstrip("/")

    well_known_url = f"https://{server_name}/.well-known/matrix/client"

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(well_known_url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    hs = data.get("m.homeserver", {})
                    if isinstance(hs, dict):
                        base_url = hs.get("base_url")
                        if isinstance(base_url, str) and base_url:
                            return base_url.rstrip("/")
        except Exception:
            pass

    return f"https://{server_name}"


async def _test_login(
    homeserver: str,
    user_id: str,
    password: str,
) -> tuple[bool, str | None, str | None, str | None]:
    """
    Test login credentials.

    Returns (success, access_token, device_id, error_message).
    """
    client = nio.AsyncClient(homeserver, user_id)
    try:
        response = await client.login(password=password, device_name="Takopi")
        if isinstance(response, nio.LoginResponse):
            return True, response.access_token, response.device_id, None
        # LoginError or other error response
        error_msg = getattr(response, "message", None) or str(response)
        return False, None, None, error_msg
    except Exception as exc:
        return False, None, None, str(exc)
    finally:
        await client.close()


async def _test_token(
    homeserver: str,
    user_id: str,
    access_token: str,
) -> bool:
    """Test if access token is valid."""
    client = nio.AsyncClient(homeserver, user_id)
    client.access_token = access_token
    client.user_id = user_id

    try:
        response = await client.sync(timeout=5000)
        return isinstance(response, nio.SyncResponse)
    except Exception:
        return False
    finally:
        await client.close()
