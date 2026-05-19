"""Business logic services for notifications.

Services are exported lazily so settings APIs can import the notifications
package without requiring optional worker dependencies such as Redis.
"""

_EXPORTS = {
    "NotificationService": ".notification_service",
    "RoutingService": ".routing_service",
    "AnalyticsService": ".analytics_service",
    "ReplayService": ".replay_service",
    "TemplateService": ".template_service",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    module = import_module(_EXPORTS[name], __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
