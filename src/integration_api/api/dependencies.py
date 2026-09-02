from fastapi import Request

from integration_api.adapters.base import ESwitchAdapter


def get_adapter(request: Request) -> ESwitchAdapter:
    return request.app.state.adapter  # type: ignore[no-any-return]
