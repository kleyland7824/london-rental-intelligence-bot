from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


_NON_PHOTO_PATH = re.compile(r"(?:^|[/_.-])(?:logo|icon|floorplan|sponsor|pixel|spacer|tracking)(?:[/_.-]|$)")


def is_listing_image_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    path = unquote(parsed.path).lower()
    if host == "media.rightmove.co.uk":
        return "/property-photo/" in path
    if host == "mns.asrvkeez.com":
        return False
    if host == "cdn.braze.eu" and path.startswith("/appboy/"):
        return False
    if host == "d15k2d11r6t6rl.cloudfront.net" and path.startswith("/public/users/integrators/"):
        return False
    return _NON_PHOTO_PATH.search(path) is None
