"""okapipy branding constants surfaced in generated artifacts.

These are the bits of the okapipy identity (repo URL, brand color, badge
images) that the generator stamps into every project it emits. They are
exposed as Jinja globals by `make_environment` so any template — today the
README, tomorrow whatever else — can render the badge / footer without
re-deriving the URLs or smuggling them through per-render context.

`OKAPIPY_LOGO_DATA_URI` is a square 128x128 white silhouette of the
okapipy mascot, transparent background, embedded as a data URI. shields.io
forces logos to render at 14x14 regardless of source aspect ratio, so a
square monochrome shape reads cleanly against the default gray label;
shields.io does not fetch remote logo URLs, only named simpleicons or
inline data URIs.
"""

from __future__ import annotations

OKAPIPY_REPO_URL = "https://github.com/ffaraone/okapipy"

OKAPIPY_BRAND_COLOR = "5b3621"
OKAPIPY_BRAND_LABEL_COLOR = "2c1a10"

OKAPIPY_FOOTER_BADGE_URL = (
    "https://raw.githubusercontent.com/ffaraone/okapipy/main/assets/badge.png"
)

OKAPIPY_LOGO_DATA_URI = (
    "data:image/png;base64,"
    "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"
)
