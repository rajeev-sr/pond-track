"""Hand memory back to the operating system after an analysis.

CPython frees objects into its own pools and glibc's, not back to the OS. An
instance that has analysed the sample sheet once stays around 100 MB above
where it started (measured on the lab systems: 165 MB idle, 263-281 MB after two
runs), and on a 512 MB system that also runs Postgres that residue is the
difference between the next analysis fitting and an OOM kill.

A full collection and then `malloc_trim(0)` return the free pages. Tens of
milliseconds, once per analysis.
"""

from __future__ import annotations

import contextlib
import ctypes
import gc
import sys


def release_memory() -> None:
    """Collect garbage, then ask glibc to return free heap pages to the OS."""
    gc.collect()
    if not sys.platform.startswith("linux"):
        return
    # Not glibc (musl, say): nothing to trim, and nothing wrong.
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL("libc.so.6").malloc_trim(0)
