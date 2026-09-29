from app.core.config import get_settings
from app.core.security import create_download_token
from app.models import Generation, GenerationImage, GenerationReference, PromptTemplate, ReferenceAsset


def image_dict(image: GenerationImage) -> dict:
    token = create_download_token(image.id)
    base = get_settings().public_base_url.rstrip("/")
    return {
        "id": image.id,
        "role": image.role,
        "mime_type": image.mime_type,
        "width": image.width,
        "height": image.height,
        "byte_size": image.byte_size,
        "download_url": f"{base}/api/v1/images/{image.id}/content?token={token}",
    }


def generation_dict(generation: Generation) -> dict:
    return {
        "id": generation.id,
        "parent_id": generation.parent_id,
        "kind": generation.kind.value,
        "status": generation.status.value,
        "prompt": generation.prompt,
        "final_prompt": generation.final_prompt,
        "model": generation.model,
        "aspect_ratio": generation.aspect_ratio,
        "resolution": generation.resolution,
        "seed": generation.seed,
        "parameters": generation.parameters,
        "error_code": generation.error_code,
        "error_message": generation.error_message,
        "created_at": generation.created_at,
        "started_at": generation.started_at,
        "completed_at": generation.completed_at,
        "images": [image_dict(image) for image in generation.images],
        "references": [reference_dict(reference) for reference in generation.references],
        "prompt_templates": [
            {"id": item.template_id, "name": item.name_snapshot, "text": item.text_snapshot, "position": item.position}
            for item in generation.template_links
        ],
    }


def reference_dict(reference: GenerationReference) -> dict:
    return {"id": reference.id, "position": reference.position, "purpose": reference.purpose, "image": image_dict(reference.image)}


def prompt_dict(template: PromptTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "text": template.text,
        "description": template.description,
        "category": template.category,
        "tags": [tag.name for tag in template.tags],
        "favorite": template.favorite,
        "sort_order": template.sort_order,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def reference_asset_dict(asset: ReferenceAsset) -> dict:
    return {
        "id": asset.id,
        "name": asset.name,
        "category": asset.category,
        "favorite": asset.favorite,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
        "image": image_dict(asset.image),
    }
