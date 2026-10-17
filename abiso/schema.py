"""The one data contract every part of Abiso shares.

A Post is what the city published, exactly as captured. An Event is what Abiso
compiled from it. A Label is the reference judgement of a Post, used to score the
compiler. Keep these three separate: the compiler never sees Labels, and the
eval never trusts an Event without a Label.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

PH_TZ = "+08:00"  # every datetime in this project is Asia/Manila, written with its offset


class DisruptionType(str, Enum):
    power = "power"                      # brownout / power interruption
    water = "water"                      # water interruption
    class_suspension = "class_suspension"
    work_suspension = "work_suspension"
    road = "road"                        # road closure, rerouting
    weather = "weather"                  # typhoon / rainfall advisory relayed by the city
    other_advisory = "other_advisory"    # a disruption that fits none of the above
    not_disruption = "not_disruption"    # events, PR, greetings: the most common post


class Status(str, Enum):
    scheduled = "scheduled"
    ongoing = "ongoing"
    extended = "extended"
    cancelled = "cancelled"
    resolved = "resolved"


class Media(BaseModel):
    kind: str                       # "image" | "video" | "link"
    url: Optional[str] = None       # remote URL as seen at capture time
    path: Optional[str] = None      # local copy under data/raw/media/
    alt: Optional[str] = None       # alt text the platform provided, if any


class Post(BaseModel):
    """One public post, captured logged out. Immutable once written."""
    post_id: str                    # platform id; the dedupe key
    page: str                       # e.g. "LLCPIO"
    url: str
    posted_at: Optional[datetime] = None   # as shown by the platform; None if not resolvable
    captured_at: datetime
    text: str = ""                  # full visible text, "See more" expanded where possible
    media: list[Media] = Field(default_factory=list)
    capture_method: str             # "dom" | "network" | "screenshot" | "website"


class Area(BaseModel):
    barangay: Optional[str] = None  # canonical Lapu-Lapu barangay name, or None if city-wide
    detail: Optional[str] = None    # sitio, street, feeder line, "portions of", as written


class Event(BaseModel):
    """What Abiso compiled from one or more Posts."""
    event_id: str
    type: DisruptionType
    status: Status = Status.scheduled
    areas: list[Area] = Field(default_factory=list)   # empty = city-wide
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    all_day: bool = False
    summary: str                    # one plain English line, e.g. "No power, Basak, Sat 8 AM to 5 PM"
    source_post_ids: list[str]      # every Post this event was compiled or updated from
    confidence: float = Field(ge=0, le=1)
    model: str                      # which model compiled it, e.g. "gemma-4-31b-it" or "gemini-..."


class Label(BaseModel):
    """The reference label for one Post, used to score the compiler. Drafted by Claude Haiku,
    checked by Claude Sonnet, spot-checked by a person (see label/ and `source`)."""
    post_id: str
    type: DisruptionType
    status: Optional[Status] = None
    barangays: list[str] = Field(default_factory=list)   # empty = city-wide or not applicable
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    all_day: bool = False
    labelled_at: datetime
    notes: str = ""
    # Free tags for the labeller's own use. Never scored.
    tags: list[str] = Field(default_factory=list)
    # "hand" = labelled from scratch; "haiku_reviewed" = a Claude Haiku draft Carl checked and saved.
    source: str = "hand"
