"""Hood Glaucoma Progression Report generator."""
from .report import HeaderFields, ProgressionReport, ReportInputs, ReportOptions  # noqa: F401

from pathlib import Path as _P

try:
    __version__ = (_P(__file__).resolve().parent.parent / "VERSION").read_text().strip()
except OSError:
    __version__ = "0.0.0"
