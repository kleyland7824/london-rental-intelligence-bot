from rental_bot.models import RawListing
from rental_bot.normalize import canonicalize_url, normalize, parse_money


def test_canonicalize_url_removes_tracking_query_and_fragment():
    assert canonicalize_url("HTTPS://Example.COM/listing/?utm_source=email&id=42#details") == "https://example.com/listing?id=42"


def test_canonicalize_portal_property_urls_without_query_variants():
    assert canonicalize_url("https://www.rightmove.co.uk/properties/12345?channel=RENT&source=email") == "https://www.rightmove.co.uk/properties/12345"
    assert canonicalize_url("https://www.zoopla.co.uk/to-rent/details/74243740/?search_identifier=abc&featured=1") == "https://www.zoopla.co.uk/to-rent/details/74243740"


def test_parse_money_handles_pounds_and_commas():
    assert parse_money("£2,150 pcm") == 2150
    assert parse_money("£500 pw") == 2167
    assert parse_money("£500 per week") == 2167


def test_normalize_infers_listing_fields():
    listing = normalize(
        RawListing(
            source="email",
            url="https://example.test/listing/1?utm_campaign=alert",
            title="Bright 2 bedroom flat in Hackney",
            description="£2,100 pcm · E8 2AB",
        )
    )
    assert listing.monthly_rent == 2100
    assert listing.bedrooms == 2
    assert listing.postcode == "E8 2AB"
    assert listing.canonical_url == "https://example.test/listing/1"


def test_normalize_accepts_an_outward_postcode_from_alert_email():
    listing = normalize(
        RawListing(
            source="email",
            url="https://example.test/listing/2",
            description="1 bed flat to rent Liverpool Road, Islington, London N7",
        )
    )

    assert listing.postcode == "N7"
