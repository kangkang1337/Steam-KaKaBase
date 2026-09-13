"""Typed failures raised by external Steam, ITAD, and image services."""


class SteamRateLimited(Exception):
    def __init__(self, message, service="steam_api"):
        super().__init__(message)
        self.service = service


class ExternalDataUnavailable(Exception):
    """The remote service responded successfully enough to be considered unavailable."""
