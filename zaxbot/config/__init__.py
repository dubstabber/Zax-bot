"""Bot-policy configuration: section layout, scratch sizes, bot names, and
the synthetic DirectPlay id range used by Phase B's queue-injection spawn
flow.

These are all knobs that can change without re-reverse-engineering the
engine (unlike ``addresses.py``). Keep raw engine VAs out of this package.

Split per feature; this facade re-exports every knob so ``cfg.X`` keeps
working for every consumer. Put a new knob in the module owning its
feature (or a new module, imported here).
"""

from .base import *  # noqa: F401,F403
from .combat import *  # noqa: F401,F403
from .movement import *  # noqa: F401,F403
from .spawn import *  # noqa: F401,F403
from .overlay import *  # noqa: F401,F403
from .pickup import *  # noqa: F401,F403
from .portal import *  # noqa: F401,F403
from .flag import *  # noqa: F401,F403
from .door import *  # noqa: F401,F403
from .switch import *  # noqa: F401,F403
from .ctf_route import *  # noqa: F401,F403
from .sk import *  # noqa: F401,F403
from .simulation import *  # noqa: F401,F403
from .lava import *  # noqa: F401,F403
from .waypoints import *  # noqa: F401,F403
