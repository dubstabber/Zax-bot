"""Named layout for the writable scratch area inside .zaxbot.

Package facade: ``model`` holds the field classes + per-bot field
tables, ``builder`` assembles the full production layout from the
per-feature block modules. Import surface is unchanged:
``ScratchField`` / ``ScratchLayout`` / ``build_scratch_layout`` /
``BOT_STATE_FIELDS`` / ``AI_PERBOT_FIELDS`` / ``AI_PERBOT_FIELD_COUNT``.
"""

from .builder import build_scratch_layout  # noqa: F401
from .from_config import build_layout_from_config  # noqa: F401
from .model import (  # noqa: F401
    AI_PERBOT_FIELD_COUNT,
    AI_PERBOT_FIELDS,
    BOT_STATE_FIELDS,
    ScratchField,
    ScratchLayout,
    _bot_state_block,
)
