import httpx
import pytest

from rental_bot.detail_viewer import DetailViewError, HttpDetailViewer


def test_viewer_extracts_visible_listing_content():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""
                <html><head><title>Bright London flat</title>
                <meta name="description" content="Two bedroom flat"></head>
                <body><h1>Bright London flat</h1><p>£2,100 pcm</p>
                <script>secret_not_visible()</script><img src="/photo.jpg"></body></html>
            """,
        )

    viewer = HttpDetailViewer(
        allowed_domains=("example.test",),
        provider_authorized_domains=("example.test",),
        transport=httpx.MockTransport(handler),
    )

    snapshot = viewer.fetch("https://example.test/listing/1")

    assert snapshot.title == "Bright London flat"
    assert snapshot.description == "Two bedroom flat"
    assert "£2,100 pcm" in snapshot.text
    assert "secret_not_visible" not in snapshot.text
    assert snapshot.image_urls == ("https://example.test/photo.jpg",)
    assert snapshot.facts["monthly_rent"] == 2100


def test_viewer_extracts_structured_listing_facts():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""<html><head><title>Listing</title>
            <script type="application/ld+json">{
              "@graph": [{"@type": "Apartment", "name": "Two-bed Hackney flat",
                "description": "Bright flat near the station",
                "address": {"streetAddress": "High Street", "addressLocality": "Hackney", "postalCode": "E8 2AB"},
                "numberOfBedrooms": 2, "numberOfBathroomsTotal": 1,
                "image": ["/living-room.jpg"],
                "offers": {"price": 500, "priceCurrency": "GBP", "unitText": "week"}
              }]}</script></head><body><main><h1>Flat to rent</h1></main></body></html>""",
        )

    viewer = HttpDetailViewer(
        allowed_domains=("example.test",),
        provider_authorized_domains=("example.test",),
        transport=httpx.MockTransport(handler),
    )

    snapshot = viewer.fetch("https://example.test/listing/1")

    assert snapshot.facts["title"] == "Two-bed Hackney flat"
    assert snapshot.facts["address"] == "High Street, Hackney, E8 2AB"
    assert snapshot.facts["monthly_rent"] == 2167
    assert snapshot.facts["bedrooms"] == 2
    assert snapshot.facts["bathrooms"] == 1
    assert snapshot.image_urls == ("https://example.test/living-room.jpg",)


def test_viewer_extracts_gallery_images_and_explicit_availability():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""<html><head><meta property="og:image" content="/front.jpg">
            <script type="application/ld+json">{"@type":"Apartment","image":[{"url":"/kitchen.jpg"}]}</script>
            </head><body><main><h1>Two bedroom flat</h1><p>Available from 12 October 2026</p>
            <img src="/logo_icon.png"><img data-src="/bedroom.jpg"
            srcset="/bedroom-small.jpg 320w, /bedroom-large.jpg 1200w"></main></body></html>""",
        )

    viewer = HttpDetailViewer(
        allowed_domains=("example.test",),
        provider_authorized_domains=("example.test",),
        transport=httpx.MockTransport(handler),
    )
    snapshot = viewer.fetch("https://example.test/listing/1")

    assert snapshot.facts["available_from"] == "2026-10-12"
    assert snapshot.image_urls == (
        "https://example.test/front.jpg", "https://example.test/kitchen.jpg",
        "https://example.test/bedroom.jpg", "https://example.test/bedroom-small.jpg",
        "https://example.test/bedroom-large.jpg",
    )


def test_viewer_rejects_access_challenge():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<title>Verify you are human</title>")

    viewer = HttpDetailViewer(
        allowed_domains=("example.test",),
        provider_authorized_domains=("example.test",),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(DetailViewError, match="access challenge"):
        viewer.fetch("https://example.test/listing/1")


def test_viewer_rejects_unapproved_domain():
    viewer = HttpDetailViewer(
        allowed_domains=("example.test",),
        provider_authorized_domains=("example.test",),
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )

    with pytest.raises(DetailViewError, match="DETAIL_ALLOWED_DOMAINS"):
        viewer.fetch("https://not-approved.test/listing/1")


def test_viewer_rejects_robots_disallow():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    viewer = HttpDetailViewer(
        allowed_domains=("example.test",),
        provider_authorized_domains=("example.test",),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(DetailViewError, match="robots.txt disallows"):
        viewer.fetch("https://example.test/listing/1")


def test_viewer_rejects_robots_redirect():
    viewer = HttpDetailViewer(
        allowed_domains=("example.test",),
        provider_authorized_domains=("example.test",),
        transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "/other"})),
    )

    with pytest.raises(DetailViewError, match="robots.txt \\(redirected\\)"):
        viewer.fetch("https://example.test/listing/1")
