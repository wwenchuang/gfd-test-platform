"""Route registration for the isolated API testing HTTP boundary."""

from .http import API_PREFIX, dispatch_delete, dispatch_get, dispatch_post, dispatch_put
from .load_agent_http import (
    AGENT_API_PREFIX,
    dispatch_get as dispatch_agent_get,
    dispatch_post as dispatch_agent_post,
)


def register_api_testing_routes(route_get_prefix, route_post_prefix, route_delete_prefix, route_put_prefix):
    """Mount the module once without placing API testing logic in router.py."""
    route_get_prefix(AGENT_API_PREFIX)(dispatch_agent_get)
    route_post_prefix(AGENT_API_PREFIX)(dispatch_agent_post)
    route_get_prefix(API_PREFIX)(dispatch_get)
    route_post_prefix(API_PREFIX)(dispatch_post)
    route_delete_prefix(API_PREFIX)(dispatch_delete)
    route_put_prefix(API_PREFIX)(dispatch_put)
