import logging
from django.core.files.storage import default_storage
from django.db import transaction
from .models import PendingImageDeletion

logger = logging.getLogger(__name__)


def delete_pending_image(deletion_id):
    # Keep the row until storage deletion succeeds, allowing retries.
    with transaction.atomic():
        deletion = PendingImageDeletion.objects.select_for_update().filter(pk=deletion_id).first()
        if deletion is None:
            return True
        try:
            default_storage.delete(deletion.file_name)
        except Exception:
            logger.warning("Image deletion pending: id=%s", deletion.pk)
            return False
        deletion.delete()
        return True


def schedule_image_deletion(file_name):
    if not file_name:
        return
    deletion, _ = PendingImageDeletion.objects.get_or_create(file_name=file_name)
    transaction.on_commit(lambda: delete_pending_image(deletion.pk), robust=True)
