"""
YouTube transcript adapter — public captions via the timedtext endpoint.

A typed operation (`transcript(url)`), not a model-generated `yt-dlp` shell
command.  Only public caption tracks are used; videos without captions
surface an honest AdapterUnavailable instead of a fake transcript.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

from helios.web.adapters.base import BaseSourceAdapter
from helios.web.types import (
    AdapterUnavailable,
    SourceCapabilities,
    SourceDocument,
    WebAccessRequest,
)

_VIDEO_ID = re.compile(r"^[\w\-]{11}$")


def extract_video_id(url: str) -> str | None:
    """Support youtube.com/watch?v=, youtu.be/, /shorts/, /embed/ and bare ids."""
    if _VIDEO_ID.match(url or ""):
        return url
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if host in ("youtu.be",):
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if _VIDEO_ID.match(candidate) else None
    if host.endswith("youtube.com"):
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
            return candidate if _VIDEO_ID.match(candidate) else None
        for prefix in ("/shorts/", "/embed/", "/live/"):
            if parsed.path.startswith(prefix):
                candidate = parsed.path[len(prefix):].split("/")[0]
                return candidate if _VIDEO_ID.match(candidate) else None
    return None


def parse_timedtext(xml_text: str) -> str:
    """Flatten a timedtext XML caption track into plain text."""
    root = ET.fromstring(xml_text)
    lines = []
    for node in root.iter("text"):
        text = "".join(node.itertext()).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


class YouTubeTranscriptAdapter(BaseSourceAdapter):
    name = "youtube"
    version = "0.1.0"
    trust_level = "builtin"
    capabilities = SourceCapabilities(transcript=True)

    TIMEDTEXT = "https://video.google.com/timedtext"

    def transcript(self, request: WebAccessRequest) -> SourceDocument:
        video_id = extract_video_id(request.url or "")
        if not video_id:
            raise AdapterUnavailable("could not extract a YouTube video id from the URL")

        xml_text = self._get_text(
            self.TIMEDTEXT, params={"lang": "en", "v": video_id}
        )
        if not xml_text.strip():
            raise AdapterUnavailable(
                f"no public English captions available for video {video_id}"
            )
        content = parse_timedtext(xml_text)
        if not content:
            raise AdapterUnavailable(
                f"caption track for video {video_id} was empty"
            )

        url = f"https://www.youtube.com/watch?v={video_id}"
        return SourceDocument(
            source=self.name,
            operation="transcript",
            url=url,
            title=f"Transcript: {video_id}",
            content=content,
            content_type="transcript",
            source_adapter=self.name,
            adapter_version=self.version,
            citations=[{"url": url}],
            warnings=["captions_source=public_timedtext"],
        )
