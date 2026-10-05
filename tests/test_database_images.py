from rental_bot.db import _merge_image_urls


def test_repeat_import_preserves_distinct_property_photos_and_drops_icons():
    previous = [
        "https://lid.zoocdn.com/645/430/one.jpg",
        "https://lid.zoocdn.com/645/430/two.jpg",
        "https://media.rightmove.co.uk/camera_icon.png",
    ]
    incoming = ["https://lid.zoocdn.com/645/430/one.jpg"]

    assert _merge_image_urls(previous, incoming) == [
        "https://lid.zoocdn.com/645/430/one.jpg",
        "https://lid.zoocdn.com/645/430/two.jpg",
    ]
