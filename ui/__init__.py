"""Tofu Server Manager - UI Layer.

This package contains the UI components, dialogs, and integration layer.
The UI is responsible for presentation only; business logic lives in core.
"""

from .integration import UIIntegration

__all__ = ["UIIntegration"]
