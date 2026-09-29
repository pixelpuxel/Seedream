import io

from PIL import Image

from app.core.config import get_settings
from app.services.generations import normalize_model
from app.services.images import prepare_provider_image
from app.services.provider import ArkSeedreamProvider, safe_provider_payload


def test_provider_payload_uses_official_seedream_fields():
    provider = ArkSeedreamProvider(get_settings())
    payload = provider.build_payload(model="dola-seedream-5-0-pro-260628", prompt="Eine Zeichnung", resolution="2K", aspect_ratio="4:3", references=[(b"bytes", "image/png")], parameters={"output_format": "png"})
    assert payload["model"] == "dola-seedream-5-0-pro-260628"
    assert payload["size"] == "2K"
    assert payload["response_format"] == "url"
    assert payload["image"].startswith("data:image/png;base64,")
    assert "4:3" in payload["prompt"]
    assert safe_provider_payload(payload)["image"] == "[embedded image]"


def test_legacy_model_ids_are_migrated():
    settings = get_settings()
    assert normalize_model("doubao-seedream-5-0-pro-260628", settings) == settings.seedream_model
    assert normalize_model("doubao-seedream-5-0-flash-260915", settings) == settings.seedream_flash_model
    assert normalize_model("doubao-seedream-5-0-260128", settings) == settings.seedream_lite_model


def test_provider_reference_is_normalized_to_jpeg():
    source = io.BytesIO()
    Image.new("RGBA", (80, 60), (255, 0, 0, 120)).save(source, "PNG")
    data, mime = prepare_provider_image(source.getvalue())
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (80, 60)
