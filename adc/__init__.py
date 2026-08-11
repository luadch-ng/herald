"""Herald ADC core: pure-Python Tiger/base32/login, ported from adclib."""

# Single source of the Herald version: the GUI About box (main.APP_VERSION),
# the ADC client VE field announced to the hub (connection.py), and the release
# zip names (packaging/build_release.py) all read this.
__version__ = "1.0.0"

from .base32 import from_base32, to_base32
from .hashes import cid_from_pid, hash_pas
from .protocol import escape, new_identity, unescape
from .tiger import tiger

__all__ = [
    "__version__",
    "tiger",
    "to_base32",
    "from_base32",
    "cid_from_pid",
    "hash_pas",
    "escape",
    "unescape",
    "new_identity",
]
