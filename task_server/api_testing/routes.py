"""Route registration for the isolated API testing HTTP boundary."""

from .http import API_PREFIX, dispatch_delete, dispatch_get, dispatch_post


def register_api_testing_routes(route_get_prefix, route_post_prefix, route_delete_prefix):
    """Mount the module once without placing API testing logic in router.py."""
    route_get_prefix(API_PREFIX)(dispatch_get)
    route_post_prefix(API_PREFIX)(dispatch_post)
    route_delete_prefix(API_PREFIX)(dispatch_delete)
