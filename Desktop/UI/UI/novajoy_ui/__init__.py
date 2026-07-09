"""NovaJoy shared UI theme and components for Kivy clients."""

from .brand import BrandHeaderBar, brand_asset_path
from .components import (
    ControlTopBar,
    MessageStream,
    NovaJoyCard,
    NovaJoyStatusCard,
    StatusStrip,
)
from .theme import (
    C,
    apply_label_muted,
    apply_label_primary,
    apply_root_background,
    apply_window_theme,
    style_button,
    style_popup,
    style_readonly_log,
    style_text_input,
)
from .widgets import (
    nvj_button,
    nvj_field_label,
    nvj_input,
    nvj_section_title,
    show_nvj_popup,
)

from .mobile import BottomNavigation, MobileTopBar

__all__ = [
    "C",
    "BrandHeaderBar",
    "ControlTopBar",
    "NovaJoyCard",
    "NovaJoyStatusCard",
    "StatusStrip",
    "MessageStream",
    "brand_asset_path",
    "apply_window_theme",
    "apply_root_background",
    "apply_label_primary",
    "apply_label_muted",
    "style_button",
    "style_text_input",
    "style_readonly_log",
    "style_popup",
    "nvj_section_title",
    "nvj_field_label",
    "nvj_input",
    "nvj_button",
    "show_nvj_popup",
    "MobileTopBar",
    "BottomNavigation",
]
