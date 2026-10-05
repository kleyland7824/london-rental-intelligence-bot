from datetime import datetime, timezone

from rental_bot.dashboard import filter_reasons, image_urls, reason_counts, matches_filters


def listing(**overrides):
    row = {
        "id": 1,
        "source": "rental-alert-email",
        "url": "https://example.test/listing/1",
        "title": "One bedroom flat in Islington",
        "address": "Islington, London N1",
        "postcode": "N1",
        "monthly_rent": 2000,
        "bedrooms": 1,
        "bathrooms": None,
        "description": "A bright flat",
        "available_from": None,
        "first_seen_at": datetime.now(timezone.utc),
        "commute_minutes": 35,
        "latitude": 51.53,
        "longitude": -0.1,
        "digest_sent_at": None,
        "review_status": "unreviewed",
        "metadata": {"image_urls": ["https://example.test/one.jpg", "bad-value"]},
    }
    row.update(overrides)
    return row


def filters(**overrides):
    values = {
        "max_rent": 2200,
        "min_bedrooms": 1,
        "areas": ("Islington",),
        "max_commute": 45,
        "status": None,
        "only_unprocessed": False,
    }
    values.update(overrides)
    return values


def test_dashboard_filters_use_viability_criteria():
    assert matches_filters(listing(), filters())
    assert not matches_filters(listing(monthly_rent=2300), filters())
    assert not matches_filters(listing(review_status="rejected"), filters(status="unreviewed"))


def test_image_urls_only_returns_http_urls():
    assert image_urls(listing()) == ["https://example.test/one.jpg"]
    assert image_urls(listing(metadata={
        "image_urls": ["https://example.test/email.jpg"],
        "page_detail": {"image_urls": ["https://example.test/page.jpg"]},
    })) == ["https://example.test/page.jpg", "https://example.test/email.jpg"]


def test_image_urls_discard_email_icons_and_keep_property_photos():
    assert image_urls(listing(metadata={
        "image_urls": [
            "https://media.rightmove.co.uk/camera_icon.png",
            "https://media.rightmove.co.uk/property-photo/123/photo.jpeg",
            "https://media.rightmove.co.uk/floorplan_icon.png",
            "https://media.rightmove.co.uk/property-photo/123/photo.jpeg",
            "https://st.zoocdn.com/zoopla_static_agent_logo.png",
            "https://lid.zoocdn.com/645/430/house.jpg",
            "https://d15k2d11r6t6rl.cloudfront.net/public/users/Integrators/banner.png",
        ],
    })) == [
        "https://media.rightmove.co.uk/property-photo/123/photo.jpeg",
        "https://lid.zoocdn.com/645/430/house.jpg",
    ]


def test_image_urls_fall_back_to_alert_when_page_has_no_property_photo():
    assert image_urls(listing(metadata={
        "image_urls": ["https://lid.zoocdn.com/645/430/house.jpg"],
        "page_detail": {"image_urls": ["https://st.zoocdn.com/agent_logo.png"]},
    })) == ["https://lid.zoocdn.com/645/430/house.jpg"]


def test_exclusion_reasons_are_independent_of_review_status():
    rejected = listing(review_status="rejected", monthly_rent=2300, commute_minutes=None)

    assert filter_reasons(rejected, filters()) == [
        "Rent too high or missing",
        "Commute too long or missing",
    ]
    assert reason_counts([rejected], filters()) == {
        "Rent too high or missing": 1,
        "Bedrooms below minimum or missing": 0,
        "Area not in selected areas": 0,
        "Commute too long or missing": 1,
    }
