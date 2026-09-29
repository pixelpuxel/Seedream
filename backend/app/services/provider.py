import base64
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings


class ProviderError(RuntimeError):
    def __init__(self, code: str, user_message: str, status_code: int = 502, payload: dict[str, Any] | None = None):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.status_code = status_code
        self.payload = payload or {}


@dataclass
class ProviderResult:
    status_code: int
    payload: dict[str, Any]


ASPECT_INSTRUCTIONS = {
    "1:1": "Erzeuge das Ergebnis quadratisch im Seitenverhältnis 1:1.",
    "4:3": "Erzeuge das Ergebnis im Querformat mit Seitenverhältnis 4:3.",
    "3:4": "Erzeuge das Ergebnis im Hochformat mit Seitenverhältnis 3:4.",
    "16:9": "Erzeuge das Ergebnis im breiten Querformat mit Seitenverhältnis 16:9.",
    "9:16": "Erzeuge das Ergebnis im Hochformat mit Seitenverhältnis 9:16.",
    "3:2": "Erzeuge das Ergebnis im Querformat mit Seitenverhältnis 3:2.",
    "2:3": "Erzeuge das Ergebnis im Hochformat mit Seitenverhältnis 2:3.",
    "21:9": "Erzeuge das Ergebnis im ultrabreiten Seitenverhältnis 21:9.",
}


class ArkSeedreamProvider:
    def __init__(self, settings: Settings, api_key: str | None = None):
        self.settings = settings
        self.api_key = (api_key if api_key is not None else settings.ark_api_key).strip()

    def build_payload(
        self,
        *,
        model: str,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        references: list[tuple[bytes, str]],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        final_prompt = prompt.strip()
        if aspect_ratio in ASPECT_INSTRUCTIONS:
            final_prompt = f"{final_prompt}\n\n{ASPECT_INSTRUCTIONS[aspect_ratio]}"
        elif aspect_ratio == "auto" and references:
            final_prompt = f"{final_prompt}\n\nWähle anhand der Referenzbilder und des Motivs ein passendes Seitenverhältnis."
        payload: dict[str, Any] = {
            "model": model,
            "prompt": final_prompt,
            "size": resolution,
            "response_format": "url",
            "watermark": bool(parameters.get("watermark", self.settings.seedream_watermark)),
            "output_format": parameters.get("output_format", "png"),
        }
        if references:
            images = [f"data:{mime};base64,{base64.b64encode(data).decode()}" for data, mime in references]
            payload["image"] = images[0] if len(images) == 1 else images
        allowed = {"background", "optimize_prompt_options", "layer_decomposition"}
        for key in allowed:
            if key in parameters:
                payload[key] = parameters[key]
        return payload

    def generate(self, payload: dict[str, Any]) -> ProviderResult:
        if not self.api_key:
            raise ProviderError("provider_not_configured", "Seedream ist noch nicht konfiguriert. Bitte den API-Key unter Einstellungen hinterlegen.", 503)
        try:
            with httpx.Client(timeout=self.settings.provider_timeout_seconds) as client:
                response = client.post(
                    self.settings.seedream_endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", "Seedream hat nicht rechtzeitig geantwortet. Bitte erneut versuchen.", 504) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_unreachable", "Seedream ist momentan nicht erreichbar.", 502) from exc
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:4000]}
        if response.is_error:
            provider_code = str(body.get("error", {}).get("code", "provider_error"))
            provider_message = str(body.get("error", {}).get("message", ""))
            user_message = self._translate_error(response.status_code, provider_code, provider_message)
            raise ProviderError(provider_code, user_message, response.status_code, body)
        if not isinstance(body.get("data"), list) or not body["data"]:
            raise ProviderError("empty_provider_response", "Seedream hat kein Bild geliefert.", 502, body)
        return ProviderResult(response.status_code, body)

    @staticmethod
    def _translate_error(status: int, code: str, message: str) -> str:
        lowered = f"{code} {message}".lower()
        if status in (401, 403):
            return "Die Seedream-Anmeldung ist ungültig oder nicht berechtigt."
        if status == 429:
            return "Das Seedream-Limit ist gerade erreicht. Bitte kurz warten und erneut versuchen."
        if "sensitive" in lowered or "content" in lowered and "policy" in lowered:
            return "Die Anfrage wurde durch die Inhaltsrichtlinien des Bildanbieters abgelehnt."
        if status == 400:
            return "Seedream konnte diese Bild- oder Parameterkombination nicht verarbeiten."
        return "Die Bildgenerierung ist beim Anbieter fehlgeschlagen."


def safe_provider_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    image = result.get("image")
    if isinstance(image, list):
        result["image"] = [f"[embedded image {index + 1}]" for index in range(len(image))]
    elif isinstance(image, str):
        result["image"] = "[embedded image]"
    return result


def parse_size(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    match = re.fullmatch(r"(\d+)x(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)
