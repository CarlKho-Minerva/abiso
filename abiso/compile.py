"""Post -> typed Events (subproblem 2). One model call per post.

    events = compile_post(post, backend)

The model fills `ModelOutput` (a reduced Event schema: no ids, no provenance,
places as written). Code then does everything deterministic: time zones, place
canonicalisation, ids, source_post_ids, the model name. Output that fails the
schema is sent back once with the validation error; a second failure raises
CompileError. Nothing is dropped silently: a post that is not a disruption
comes back as one Event of type `not_disruption`, so the classification is
recorded with its provenance and confidence.

Two backends share one interface (`Backend.generate`):
  GemmaVLLM  - Carl's PC, OpenAI-compatible vLLM, model gemma-4-31b-it
  Gemini     - google-genai, key read at call time from ~/.config/carl-life-os/.env
Tests use a fake backend; nothing here runs a model on import.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, model_validator

from abiso.places_match import PlaceMatcher, load_barangays
from abiso.schema import DisruptionType, Event, Post, Status

MANILA = ZoneInfo("Asia/Manila")
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path.home() / ".config" / "carl-life-os" / ".env"


# --------------------------------------------------------------------------- #
# What the model must return
# --------------------------------------------------------------------------- #
# Every field is required (nullable where it can be absent). With optional
# fields, vLLM's guided decoding skipped `areas` entirely in the 10-03 smoke test.
class ModelArea(BaseModel):
    place: Optional[str] = Field(
        ..., description="Barangay or place exactly as the notice names it, e.g. 'portions of Brgy. Basak'")
    detail: Optional[str] = Field(
        ..., description="Sitio, street, feeder line or landmark as written; null if none")


class ModelEvent(BaseModel):
    type: DisruptionType
    status: Status
    areas: list[ModelArea] = Field(
        ..., description="One entry per barangay or place named; empty list ONLY if the notice covers the whole city")
    start: Optional[datetime] = Field(
        ..., description="ISO 8601 with +08:00, e.g. 2026-10-04T08:00:00+08:00; null if the notice gives no time")
    end: Optional[datetime] = Field(..., description="ISO 8601 with +08:00; null if not stated")
    all_day: bool
    summary: str = Field(..., min_length=1, description="One plain English line, e.g. 'No power, Basak, Sat 8 AM to 5 PM'")
    confidence: float = Field(..., ge=0, le=1)

    @model_validator(mode="after")
    def _end_after_start(self) -> "ModelEvent":
        if self.start and self.end and to_manila(self.end) < to_manila(self.start):
            raise ValueError(f"end {self.end} is before start {self.start}")
        return self


class ModelOutput(BaseModel):
    events: list[ModelEvent] = Field(
        ..., min_length=1,
        description="One entry per distinct disruption. A post that is not a disruption gets exactly one entry of type not_disruption.")


def _inline_refs(schema: dict) -> dict:
    """Pydantic emits $defs/$ref; both vLLM's grammar backend and Gemini are
    happier with a self-contained schema, so inline every reference."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return walk(defs[name])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def output_json_schema() -> dict:
    return _inline_refs(ModelOutput.model_json_schema())


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def to_manila(dt: datetime) -> datetime:
    """Naive datetimes are read as Manila wall time; aware ones are converted."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MANILA)
    return dt.astimezone(MANILA)


_WEEKDAYS = [  # English, Filipino, Bisaya
    ("Monday", "Lunes", "Lunes"),
    ("Tuesday", "Martes", "Martes"),
    ("Wednesday", "Miyerkules", "Miyerkoles"),
    ("Thursday", "Huwebes", "Huwebes"),
    ("Friday", "Biyernes", "Biyernes"),
    ("Saturday", "Sabado", "Sabado"),
    ("Sunday", "Linggo", "Domingo"),
]

RELATIVE_WORDS: dict[int, tuple[str, ...]] = {
    -1: ("yesterday", "kahapon", "gahapon", "kagahapon"),
    0: ("today", "ngayon", "ngayong araw", "karon", "karong adlawa", "run"),
    1: ("tomorrow", "bukas", "ugma"),
    2: ("day after tomorrow", "sa makalawa", "samakalawa", "sunod ugma", "puhon ugma"),
}


def anchor_date(post: Post) -> Optional[date]:
    return to_manila(post.posted_at).date() if post.posted_at else None


def resolve_relative(word: str, posted: date) -> Optional[date]:
    """Deterministic resolver used to build the prompt table (and testable alone)."""
    w = word.strip().lower()
    for offset, words in RELATIVE_WORDS.items():
        if w in words:
            return posted + timedelta(days=offset)
    for i, names in enumerate(_WEEKDAYS):
        if w in (n.lower() for n in names):
            ahead = (i - posted.weekday()) % 7
            return posted + timedelta(days=ahead)  # same weekday = the posting day
    return None


def date_table(post: Post) -> str:
    d = anchor_date(post)
    if d is None:
        return ("The posting date is UNKNOWN. Do not resolve relative words such as "
                "'bukas', 'ugma', 'tomorrow' or bare weekdays; leave start/end null unless "
                "the notice states an absolute date.")
    lines = [f"Posted on {d.isoformat()} ({_WEEKDAYS[d.weekday()][0]}), Asia/Manila (UTC+08:00)."]
    for offset in (-1, 0, 1, 2):
        lines.append(f"- {' / '.join(RELATIVE_WORDS[offset])} = {(d + timedelta(days=offset)).isoformat()}")
    for i in range(7):
        day = d + timedelta(days=i)
        lines.append(f"- {' / '.join(dict.fromkeys(_WEEKDAYS[day.weekday()]))} (next) = {day.isoformat()}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You read one official public post from an issuer that serves Lapu-Lapu City, \
Cebu, Philippines (the city government, the power or water utility, the DepEd division, a \
barangay, PAGASA) and turn it into structured disruption events. Posts may be in \
Bisaya/Cebuano, Filipino, English or a mix, and the key facts are often only in the image.
The issuer tells you what to expect (a power utility mostly posts power interruptions), \
but the content decides: a utility can post a greeting, and the city relays every type.

Types: power (brownout, power interruption), water (water interruption), class_suspension, \
work_suspension, road (closure, rerouting), weather (typhoon or rainfall advisory), \
other_advisory (a disruption that fits none of these), not_disruption (events, PR, \
greetings, programmes: the most common post).

Rules:
- One event per distinct disruption (different type, area set or time window). A brownout \
schedule listing three areas at three times is three events.
- If the post is not a disruption, return exactly one event of type not_disruption, \
with empty areas, null times, and a summary of what the post is.
- Status: scheduled (announced ahead), ongoing, extended, cancelled, resolved \
(e.g. "power restored", "classes resume").
- Times: ISO 8601 with +08:00. Resolve relative dates ONLY with the date table given. \
Never invent a time the notice does not state: use null. A whole-day suspension is \
all_day=true with start at 00:00 of that day and end null.
- Areas: copy the place as written (keep "portions of"), one area per barangay. \
Empty list = the whole of Lapu-Lapu City. Feeder lines, sitios and streets go in detail. \
Places outside Lapu-Lapu (e.g. Cordova, Cebu City) are still listed as written.
- confidence: your probability that every field is right.
- summary: one plain English line.
Return JSON only, matching the schema."""


def _barangay_hint(barangays: list[str]) -> str:
    return "Lapu-Lapu City barangays: " + ", ".join(barangays) + "."


# Issuer context by page (spec section 2a). Keys are matched as whole words in
# the normalised page name, in insertion order (specific issuers before generic
# words like "water" or the city name), so the collect agent's page ids need not
# be exact. The issuer list is "to verify during collection"; add pages here.
ISSUER_HINTS: dict[str, str] = {
    "meco": "Mactan Electric Company (MECO), the power distributor for Lapu-Lapu City and Cordova. "
            "Mostly power interruptions; areas are often feeders and may include Cordova.",
    "mactan electric": "Mactan Electric Company (MECO), the power distributor for Lapu-Lapu City and Cordova. "
                       "Mostly power interruptions; areas are often feeders and may include Cordova.",
    "ngcp": "National Grid Corporation of the Philippines: grid alerts (yellow/red) and transmission "
            "outages, usually region-wide power notices.",
    "mcwd": "Metropolitan Cebu Water District: water interruptions and low pressure advisories; "
            "covers several cities, so check which areas are in Lapu-Lapu.",
    "deped": "DepEd Lapu-Lapu City Division: class suspensions and school advisories.",
    "pagasa": "PAGASA, the national weather bureau: weather advisories (typhoon signals, rainfall), "
              "usually province- or region-wide.",
    "dpwh": "Department of Public Works and Highways: road closures and repairs.",
    "barangay": "A barangay's own page: notices for that barangay (water, power, roads, events).",
    "brgy": "A barangay's own page: notices for that barangay (water, power, roads, events).",
    "water": "A water provider: mostly water interruptions.",
    "llcpio": "Lapu-Lapu City Public Information Office (the city government): relays every type, "
              "decides class and work suspensions, road closures.",
    "lapu lapu city": "Lapu-Lapu City government: relays every type, decides class and work "
                      "suspensions, road closures.",
}


def issuer_hint(page: str) -> str:
    from abiso.issuers import issuer_of
    from abiso.places_match import normalise
    known = issuer_of(page)                 # exact page id first (collect/SOURCES.md has the evidence)
    if known and known in ISSUER_HINTS:
        return ISSUER_HINTS[known]
    p = f" {normalise(page)} "
    for key in ISSUER_HINTS:
        if f" {normalise(key)} " in p:      # whole words: "meco" never matches "homecoming"
            return ISSUER_HINTS[key]
    return "Unknown issuer: infer the type from the content."


def build_user_text(post: Post, barangays: list[str], include_alt: bool = True) -> str:
    parts = [
        f"Page: {post.page}",
        f"Issuer: {issuer_hint(post.page)}",
        f"URL: {post.url}",
        f"Posted at: {to_manila(post.posted_at).isoformat() if post.posted_at else 'unknown'}",
        "",
        "Date table:",
        date_table(post),
        "",
        _barangay_hint(barangays),
        "",
        "Post text:",
        post.text.strip() or "(no text)",
    ]
    images = [m for m in post.media if m.kind == "image"]
    if images:
        parts.append("")
        parts.append(f"The post has {len(images)} image(s).")
        if include_alt:
            for i, m in enumerate(images, 1):
                if m.alt:
                    parts.append(f"Image {i} alt text: {m.alt}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
@dataclass
class ImagePart:
    mime: str
    data: bytes

    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode()}"


@dataclass
class Turn:
    role: str                      # "user" | "model"
    text: str
    images: list[ImagePart] = field(default_factory=list)


@dataclass
class ModelReply:
    text: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None     # includes thinking tokens where billed
    latency_s: float = 0.0
    raw_usage: dict = field(default_factory=dict)


class Backend(Protocol):
    model: str
    supports_images: bool

    def generate(self, system: str, turns: list[Turn], schema: dict) -> ModelReply: ...


class GemmaVLLM:
    """OpenAI-compatible vLLM on Carl's PC. Shared with his nightly jobs: keep
    concurrency at 1 and batches small."""

    def __init__(self, base_url: Optional[str] = None, model: str = "gemma-4-31b-it",
                 supports_images: bool = True, timeout_s: float = 180.0, max_tokens: int = 2048,
                 max_images: int = 1):
        # ABISO_VLLM_URL from the environment or the env file; the server's address stays out of the repo.
        base_url = base_url or env_value("ABISO_VLLM_URL", "http://localhost:8000/v1")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.supports_images = supports_images
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.max_images = max_images

    def _messages(self, system: str, turns: list[Turn]) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for t in turns:
            role = "assistant" if t.role == "model" else "user"
            if t.images and self.supports_images:
                # Carl's PC vLLM runs --limit-mm-per-prompt image=1: send the first image only.
                content: Any = [{"type": "text", "text": t.text}] + [
                    {"type": "image_url", "image_url": {"url": im.data_url()}} for im in t.images[:self.max_images]]
            else:
                content = t.text
            msgs.append({"role": role, "content": content})
        return msgs

    def generate(self, system: str, turns: list[Turn], schema: dict) -> ModelReply:
        import httpx
        body = {
            "model": self.model,
            "messages": self._messages(system, turns),
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "abiso_events", "schema": schema}},
        }
        t0 = time.monotonic()
        r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        return ModelReply(
            text=data["choices"][0]["message"]["content"] or "",
            model=self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_s=time.monotonic() - t0,
            raw_usage=usage,
        )


def env_value(name: str, default: str, env_file: Path = ENV_FILE) -> str:
    """A non-secret setting from the environment or the env file, else the default."""
    try:
        return read_env_key(name, env_file)
    except RuntimeError:
        return default


def read_env_key(name: str, env_file: Path = ENV_FILE) -> str:
    """Read one key from Carl's env file at call time. Never logged, never cached on disk."""
    if os.environ.get(name):
        return os.environ[name]
    if not env_file.exists():
        raise RuntimeError(f"{name} not set and {env_file} does not exist")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise RuntimeError(f"{name} not found in {env_file}")


class Gemini:
    """google-genai. Paid: every call costs money, see finetune/README and the
    smoke-test log before running anything in bulk."""

    def __init__(self, model: str = "gemini-3.8-flash", thinking_level: Optional[str] = "low",
                 max_output_tokens: int = 4096):
        self.model = model
        self.supports_images = True
        self.thinking_level = thinking_level
        self.max_output_tokens = max_output_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"1", "true", "yes"}:
                self._client = genai.Client()   # Vertex AI: GOOGLE_CLOUD_PROJECT/LOCATION + runtime credentials
            else:
                self._client = genai.Client(api_key=read_env_key("GEMINI_API_KEY"))
        return self._client

    def generate(self, system: str, turns: list[Turn], schema: dict) -> ModelReply:
        from google.genai import types
        contents = []
        for t in turns:
            parts = [types.Part.from_text(text=t.text)]
            parts += [types.Part.from_bytes(data=im.data, mime_type=im.mime) for im in t.images]
            contents.append(types.Content(role="model" if t.role == "model" else "user", parts=parts))
        cfg: dict[str, Any] = dict(
            system_instruction=system,
            temperature=0,
            max_output_tokens=self.max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=schema,
        )
        if self.thinking_level:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=self.thinking_level)
        t0 = time.monotonic()
        resp = self._get_client().models.generate_content(
            model=self.model, contents=contents, config=types.GenerateContentConfig(**cfg))
        um = resp.usage_metadata
        out = (um.candidates_token_count or 0) + (um.thoughts_token_count or 0) if um else None
        return ModelReply(
            text=resp.text or "",
            model=self.model,
            input_tokens=um.prompt_token_count if um else None,
            output_tokens=out,
            latency_s=time.monotonic() - t0,
            raw_usage=um.model_dump(exclude_none=True) if um else {},
        )


# --------------------------------------------------------------------------- #
# Compile
# --------------------------------------------------------------------------- #
class CompileError(RuntimeError):
    def __init__(self, post_id: str, message: str, replies: list[ModelReply]):
        super().__init__(f"compile failed for post {post_id}: {message}")
        self.post_id = post_id
        self.replies = replies


class MediaError(RuntimeError):
    pass


@dataclass
class CompileResult:
    events: list[Event]
    replies: list[ModelReply]           # one per model call (1, or 2 after a retry)
    used_images: bool


def load_image(media, fetch_timeout_s: float = 30.0) -> ImagePart:
    """Local copy first (path relative to the repo root or absolute), else the URL."""
    if media.path:
        p = Path(media.path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if p.exists():
            mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
            return ImagePart(mime=mime, data=p.read_bytes())
    if media.url:
        import httpx
        try:
            r = httpx.get(media.url, timeout=fetch_timeout_s, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise MediaError(f"image fetch failed ({type(e).__name__}): {media.url[:120]}") from e
        mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
        return ImagePart(mime=mime, data=r.content)
    raise MediaError(f"image has neither a readable path nor a url: {media!r}")


def parse_reply(text: str) -> ModelOutput:
    """Strict: JSON object matching ModelOutput. Tolerates a ```json fence only."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    return ModelOutput.model_validate(json.loads(s))


def event_id(post_id: str, index: int, ev: ModelEvent) -> str:
    key = f"{post_id}|{index}|{ev.type.value}|{ev.start.isoformat() if ev.start else ''}"
    return "ev_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def to_events(post: Post, out: ModelOutput, model: str, matcher: PlaceMatcher) -> list[Event]:
    events = []
    for i, me in enumerate(out.events):
        areas = []
        if me.type != DisruptionType.not_disruption:
            for a in me.areas:
                for area in matcher.to_areas(a.place, a.detail):
                    if area not in areas:
                        areas.append(area)
        events.append(Event(
            event_id=event_id(post.post_id, i, me),
            type=me.type,
            status=me.status,
            areas=areas,
            start=to_manila(me.start) if me.start else None,
            end=to_manila(me.end) if me.end else None,
            all_day=me.all_day,
            summary=me.summary.strip(),
            source_post_ids=[post.post_id],
            confidence=me.confidence,
            model=model,
        ))
    return events


def compile_post_detailed(post: Post, backend: Backend, barangays: Optional[list[str]] = None,
                          images: Optional[list[ImagePart]] = None) -> CompileResult:
    barangays = barangays if barangays is not None else load_barangays()
    matcher = PlaceMatcher(barangays)
    image_media = [m for m in post.media if m.kind == "image"]
    use_images = bool(image_media) and backend.supports_images
    if use_images and images is None:
        images = [load_image(m) for m in image_media]
    # Backends without vision get the alt text instead (always included in the text).
    first = Turn("user", build_user_text(post, barangays), images if use_images else [])
    schema = output_json_schema()

    replies: list[ModelReply] = []
    turns = [first]
    last_error = ""
    for attempt in (1, 2):
        reply = backend.generate(SYSTEM_PROMPT, turns, schema)
        replies.append(reply)
        try:
            out = parse_reply(reply.text)
            return CompileResult(to_events(post, out, backend.model, matcher), replies, use_images)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            last_error = str(e)[:2000]
            if attempt == 1:
                turns = [first,
                         Turn("model", reply.text),
                         Turn("user", "That output failed validation:\n" + last_error +
                              "\nReturn the corrected JSON object only.")]
    raise CompileError(post.post_id, f"invalid output twice; last error: {last_error}", replies)


def compile_post(post: Post, backend: Backend, barangays: Optional[list[str]] = None) -> list[Event]:
    """One post -> its Events. Raises CompileError after one failed retry."""
    return compile_post_detailed(post, backend, barangays).events
