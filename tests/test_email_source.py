from email import message_from_bytes, message_from_string
from email.message import EmailMessage
import imaplib

import pytest

from rental_bot.normalize import normalize
from rental_bot.sources.email import EmailAlertSource


def test_mailbox_connect_retries_network_failures_only(monkeypatch):
    attempts = []
    delays = []

    class Mailbox:
        def login(self, username, password):
            assert (username, password) == ("user", "secret")

    def connect(host, port, timeout):
        attempts.append((host, port, timeout))
        if len(attempts) < 3:
            raise OSError("network unavailable")
        return Mailbox()

    monkeypatch.setattr("rental_bot.sources.email.imaplib.IMAP4_SSL", connect)
    monkeypatch.setattr("rental_bot.sources.email.time.sleep", delays.append)
    source = EmailAlertSource(host="mail.example.test", port=993, username="user", password="secret")

    assert isinstance(source._connect(), Mailbox)
    assert attempts == [("mail.example.test", 993, 20)] * 3
    assert delays == [1, 2]


def test_mailbox_connect_does_not_retry_rejected_credentials(monkeypatch):
    attempts = []
    closed = []

    class Mailbox:
        def login(self, username, password):
            raise imaplib.IMAP4.error(b"[ALERT] Invalid credentials (Failure)")

        def logout(self):
            closed.append(True)

    def connect(host, port, timeout):
        attempts.append(True)
        return Mailbox()

    monkeypatch.setattr("rental_bot.sources.email.imaplib.IMAP4_SSL", connect)
    source = EmailAlertSource(host="mail.example.test", port=993, username="user", password="secret")

    with pytest.raises(imaplib.IMAP4.error):
        source._connect()
    assert attempts == [True]
    assert closed == [True]


def test_email_parser_extracts_listing_provenance_and_deduplicates_tracking_urls():
    message = message_from_string(
        """From: alerts@example.test
Date: Tue, 29 Sep 2026 12:00:00 +0000
Message-ID: <alert-123@example.test>
Subject: New rental alert
Content-Type: text/html; charset=utf-8

<html><body>
<div><h2>Bright two bedroom flat</h2>
<a href="https://www.zoopla.co.uk/to-rent/details/42/?utm_content=alert">View property</a>
<p>£2,100 pcm · 2 bedrooms · E8 2AB</p>
<img src="/photo.jpg">
</div>
<div><a href="https://www.zoopla.co.uk/to-rent/details/42/?utm_content=other">View again</a>
£2,100 pcm · 2 bedrooms · E8 2AB</div>
</body></html>
"""
    )
    source = EmailAlertSource(
        host="unused",
        port=993,
        username="unused",
        password="unused",
    )

    listings = source._parse_message(message)

    assert len(listings) == 1
    listing = listings[0]
    assert listing.source_listing_id
    assert listing.title == "Bright two bedroom flat"
    assert listing.metadata["email_subject"] == "New rental alert"
    assert listing.metadata["email_message_id"] == "<alert-123@example.test>"
    assert listing.metadata["image_urls"] == ["https://www.zoopla.co.uk/photo.jpg"]
    assert listing.metadata["parser_version"] == "email-v3"
    assert listing.bedrooms == 2


def test_email_parser_finds_facts_in_a_sibling_card_cell():
    message = message_from_bytes(
        """From: alerts@example.test
Subject: New listings
Content-Type: text/html; charset=utf-8

<table><tr>
<td><a href="https://www.zoopla.co.uk/to-rent/details/42/"><img src="/photo.jpg"></a></td>
<td><p>£1,950 pcm</p><p>2 beds in Hackney</p></td>
<td><a href="https://www.zoopla.co.uk/to-rent/details/42/">View property details</a></td>
</tr></table>
""".encode()
    )
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")

    listings = source._parse_message(message)

    assert len(listings) == 1
    assert listings[0].monthly_rent == 1950
    assert normalize(listings[0]).bedrooms == 2
    assert listings[0].title == "2 beds in Hackney"


def test_email_parser_uses_nearest_single_listing_table_with_price():
    message = EmailMessage()
    message.set_content(
        """<table><tr><td><table><tr><td>
        <a href="https://www.rightmove.co.uk/properties/12345">2 bed flat in Hackney</a>
        </td></tr><tr><td>£2,100 pcm</td></tr></table></td></tr>
        <tr><td><table><tr><td>
        <a href="https://www.rightmove.co.uk/properties/67890">3 bed house in Islington</a>
        </td></tr><tr><td>£3,000 pcm</td></tr></table></td></tr></table>""",
        subtype="html",
    )
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")

    listings = source._parse_message(message)

    assert len(listings) == 2
    assert [listing.monthly_rent for listing in listings] == [2100, 3000]
    assert [listing.bedrooms for listing in listings] == [2, 3]


def test_email_parser_reuses_image_from_same_property_card():
    message = EmailMessage()
    message.set_content(
        """<table><tr><td><a href="https://www.rightmove.co.uk/properties/12345?channel=RENT">
        <img src="https://images.example.test/flat.jpg"></a></td></tr>
        <tr><td><table><tr><td><a href="https://www.rightmove.co.uk/properties/12345?source=email">
        2 bed flat in Hackney</a></td></tr><tr><td>£2,100 pcm</td></tr></table></td></tr></table>""",
        subtype="html",
    )
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")

    listings = source._parse_message(message)

    assert len(listings) == 1
    assert listings[0].monthly_rent == 2100
    assert listings[0].metadata["image_urls"] == ["https://images.example.test/flat.jpg"]


def test_email_parser_collects_sibling_photos_without_taking_the_next_listing():
    message = EmailMessage()
    message.set_content(
        """<table><tr><td><a href="https://www.rightmove.co.uk/properties/12345">
        <img src="https://media.rightmove.co.uk/property-photo/123/one.jpg"></a>
        <p>2 bed flat in Hackney · £2,100 pcm</p></td></tr>
        <tr><td><img src="https://media.rightmove.co.uk/property-photo/123/two.jpg"></td></tr>
        <tr><td><img src="https://media.rightmove.co.uk/property-photo/123/three.jpg"></td></tr></table>
        <table><tr><td><a href="https://www.rightmove.co.uk/properties/67890">
        1 bed flat in Islington</a><p>£1,800 pcm</p>
        <img src="https://media.rightmove.co.uk/property-photo/678/four.jpg"></td></tr></table>""",
        subtype="html",
    )
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")

    listings = source._parse_message(message)

    assert len(listings) == 2
    assert listings[0].metadata["image_urls"] == [
        "https://media.rightmove.co.uk/property-photo/123/one.jpg",
        "https://media.rightmove.co.uk/property-photo/123/two.jpg",
        "https://media.rightmove.co.uk/property-photo/123/three.jpg",
    ]
    assert listings[1].metadata["image_urls"] == [
        "https://media.rightmove.co.uk/property-photo/678/four.jpg",
    ]


def test_email_parser_ignores_icons_and_prefers_lazy_property_image():
    message = EmailMessage()
    message.set_content(
        """<article><h2>2 bed flat in Hackney</h2><p>£2,100 pcm</p>
        <a href="https://www.rightmove.co.uk/properties/12345">View property</a>
        <img src="https://media.rightmove.co.uk/camera_icon.png">
        <img src="https://media.rightmove.co.uk/floorplan_icon.png">
        <img src="https://media.rightmove.co.uk/email_icon.png">
        <img src="https://media.rightmove.co.uk/placeholder_icon.png"
             data-src="https://media.rightmove.co.uk/property-photo/123/flat.jpeg">
        </article>""",
        subtype="html",
    )
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")

    listings = source._parse_message(message)

    assert len(listings) == 1
    assert listings[0].metadata["image_urls"] == [
        "https://media.rightmove.co.uk/property-photo/123/flat.jpeg"
    ]


def test_email_parser_converts_weekly_rent_and_extracts_bathrooms():
    message = EmailMessage()
    message["Subject"] = "New rental listing"
    message.set_content(
        """<article><h2>2 bedroom flat in Hackney</h2>
<p>£500 per week · 2 beds · 1 bathroom</p>
<a href="https://www.zoopla.co.uk/to-rent/details/99/">View property</a></article>""",
        subtype="html",
    )
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")

    listing = source._parse_message(message)[0]

    assert listing.monthly_rent == 2167
    assert listing.bedrooms == 2
    assert listing.bathrooms == 1


def test_rightmove_image_first_card_uses_descriptive_link_and_local_address():
    message = EmailMessage()
    message.set_content(
        """<table><tr><td>
        <a href="https://www.rightmove.co.uk/properties/12345"><img src="https://media.rightmove.co.uk/flat.jpg"></a>
        <span>£2,100 pcm</span>
        <a href="https://www.rightmove.co.uk/properties/12345?utm_source=email">1 bedroom flat to rent</a>
        <span>Example Road, London E8</span><span>Agent information: call us today</span>
        </td></tr></table>""",
        subtype="html",
    )
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")

    listings = source._parse_message(message)

    assert len(listings) == 1
    assert listings[0].title == "1 bedroom flat to rent"
    assert listings[0].address == "Example Road, London E8"
    assert listings[0].monthly_rent == 2100


def test_email_parser_rejects_tracking_search_and_account_links():
    message = EmailMessage()
    message.set_content(
        """<article><h2>1 bedroom flat</h2><p>£2,100 pcm</p>
        <a href="https://clicks.rightmove.co.uk/f/a/tracking-token">View property</a>
        <a href="https://mns.asrvkeez.com/click/123/token">View property</a>
        <a href="https://www.zoopla.co.uk/myaccount/alerts-searches/">View property</a>
        <a href="https://www.zoopla.co.uk/to-rent/flats/1-bedroom/london/">View property</a>
        <a href="https://www.zoopla.co.uk/to-rent/details/456/">View property</a>
        </article>""",
        subtype="html",
    )
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")

    listings = source._parse_message(message)

    assert len(listings) == 1
    assert listings[0].url == "https://www.zoopla.co.uk/to-rent/details/456/"


def test_email_parser_only_records_explicit_availability():
    source = EmailAlertSource(host="unused", port=993, username="unused", password="unused")
    dated = EmailMessage()
    dated.set_content(
        """<article><h2>1 bedroom flat</h2><p>£2,000 pcm · Available from 25 October 2026</p>
        <a href="https://www.zoopla.co.uk/to-rent/details/456/">View property</a></article>""",
        subtype="html",
    )
    immediate = EmailMessage()
    immediate.set_content(
        """<article><h2>1 bedroom flat</h2><p>£2,000 pcm · Available now</p>
        <a href="https://www.zoopla.co.uk/to-rent/details/789/">View property</a></article>""",
        subtype="html",
    )

    assert source._parse_message(dated)[0].available_from.isoformat() == "2026-10-25"
    now = source._parse_message(immediate)[0]
    assert now.available_from is None
    assert now.metadata["availability_note"] == "Available now"
