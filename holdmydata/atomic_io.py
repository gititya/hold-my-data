"""Write-then-rename, so a crash mid-write never leaves a truncated file under the real name.

A plain `path.write_text(...)` (what `cli.py` and `images.py` did before this existed) can be
interrupted after some bytes land on disk. What's left looks like a finished file -- silent
partial output, the same failure mode `engine.py`'s fail-closed rule exists to prevent, just at
the filesystem layer instead of the detection layer.

`os.replace` is atomic on the same filesystem: the destination either has the old contents or
the fully-written new ones, never something in between.
"""

import os
from pathlib import Path


class WouldOverwrite(FileExistsError):
    """Refusing to silently replace an existing file. Pass force=True to allow it."""


def _tmp_path(path: Path) -> Path:
    # ".tmp" goes BEFORE the real suffix (report.tmp.md, not report.md.tmp) so anything that
    # infers format from the extension -- PIL's Image.save chief among them -- still works on
    # the temp file. cleanup_stray_tmp's *.tmp glob only matches the final suffix, so this
    # relies on there being exactly one "tmp" segment; a path with a literal ".tmp" in its
    # real name is not a case this project's files hit.
    return path.with_name(path.stem + ".tmp" + path.suffix)


def _check_clobber(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise WouldOverwrite(
            f"{path} already exists. Pass --force to overwrite it, or choose another output path."
        )


def write_text(path: Path, text: str, force: bool = False) -> None:
    path = Path(path)
    _check_clobber(path, force)
    tmp = _tmp_path(path)
    tmp.write_text(text)
    os.replace(tmp, path)


def write_bytes(path: Path, data: bytes, force: bool = False) -> None:
    path = Path(path)
    _check_clobber(path, force)
    tmp = _tmp_path(path)
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_image(image, path: Path, force: bool = False, **save_kwargs) -> None:
    """Same atomic pattern for a PIL Image, whose own `.save()` writes straight to disk."""
    path = Path(path)
    _check_clobber(path, force)
    tmp = _tmp_path(path)
    image.save(tmp, **save_kwargs)
    os.replace(tmp, path)


def cleanup_stray_tmp(directory: Path) -> list:
    """Delete leftover `.tmp` files from a process that died mid-write. Call on startup.

    Returns the paths removed, for logging -- never silent, but never noisy either.
    """
    removed = []
    for tmp in Path(directory).glob("*.tmp.*"):
        tmp.unlink()
        removed.append(tmp)
    return removed
