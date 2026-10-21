import socket
from ipaddress import ip_address
from urllib.parse import urlsplit

from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import ThreadedResolver

from app.schemas.push_subscription import PushSubscriptionEndpoint


class UnsafePushEndpointError(ValueError):
    pass


def require_public_address(host: str) -> None:
    address = ip_address(host)
    if not address.is_global or address.is_multicast:
        raise UnsafePushEndpointError("Push endpoint must use a public address")


def push_audience(endpoint: str) -> str:
    PushSubscriptionEndpoint(endpoint=endpoint)
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    if (
        host is None
        or "%" in host
        or parsed.fragment
        or "\\" in endpoint
        or any(ord(char) <= 32 or ord(char) == 127 for char in endpoint)
    ):
        raise UnsafePushEndpointError("Invalid push endpoint")
    # Accessing port also rejects malformed port numbers before network I/O.
    port = parsed.port
    try:
        ip_address(host)
    except ValueError:
        if ":" in host or host.replace(".", "").isdigit():
            raise UnsafePushEndpointError("Invalid push endpoint address") from None
    else:
        require_public_address(host)
    authority = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
    if port is not None and port != 443:
        authority += f":{port}"
    return f"https://{authority}"


class PublicPushResolver(AbstractResolver):
    def __init__(self) -> None:
        self._resolver = ThreadedResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        addresses = await self._resolver.resolve(host, port, family)
        for address in addresses:
            require_public_address(address["host"])
        return addresses

    async def close(self) -> None:
        await self._resolver.close()
