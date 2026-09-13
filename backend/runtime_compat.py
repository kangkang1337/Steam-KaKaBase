"""Temporary compatibility boundary for legacy runtime exports.

New application code must import an owning module (`db`, `steam_client`,
`catalog`, `crawler_data`, or `game_commands`) whenever one exists.  A small
number of long-lived orchestration paths still use this module while their
implementation is being retained for backwards compatibility with deployed
extensions.  Keeping that dependency here prevents new circular imports.
"""

from . import _runtime as _legacy


def __getattr__(name):
    return getattr(_legacy, name)
