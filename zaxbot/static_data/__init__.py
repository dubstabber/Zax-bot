"""Static data packing for the .zaxbot section (package facade).

``common`` holds prompts/tags/name+color writers, ``tables`` the
per-feature static-table writers, ``scratch`` the orchestrating
``write_static_scratch_data``, and ``from_config`` the cfg->kwargs
mapping used by ``hook/entry.py``.
"""

from .common import (  # noqa: F401
    DUMP_TAGS,
    PROMPT_CTF,
    PROMPT_DM,
    PROMPT_SK,
    pack_tag,
    write_bot_color_table,
    write_bot_name_tables,
)
from .scratch import write_static_scratch_data  # noqa: F401
from .tables import (  # noqa: F401
    write_door_static_table,
    write_flag_static_table,
    write_item_static_table,
    write_portal_static_table,
    write_sk_static_table,
    write_switch_static_table,
)
from .from_config import write_static_from_config  # noqa: F401
