from django.conf import settings
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError


def validate_case_images(images):
    """Validate before charging coins or storing files, including service calls."""
    if not 1 <= len(images) <= 6:
        raise ValidationError({"images": "대화 이미지를 1~6장 첨부해주세요."})
    if sum(image.size for image in images) > settings.CASE_IMAGES_MAX_TOTAL_BYTES:
        raise ValidationError({"images": "첨부 이미지의 전체 용량이 제한을 초과했습니다."})
    for image in images:
        if image.size > settings.CASE_IMAGE_MAX_BYTES:
            raise ValidationError({"images": "이미지 한 장의 용량이 제한을 초과했습니다."})
        try:
            image.seek(0)
            with Image.open(image) as decoded:
                if decoded.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValidationError({"images": "PNG, JPEG, WEBP 이미지만 첨부할 수 있습니다."})
                if decoded.width * decoded.height > settings.CASE_IMAGE_MAX_PIXELS:
                    raise ValidationError({"images": "이미지 해상도가 제한을 초과했습니다."})
                decoded.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ValidationError({"images": "올바른 이미지 파일을 첨부해주세요."}) from None
        finally:
            image.seek(0)
