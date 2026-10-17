from django.core.management.base import BaseCommand, CommandError
from cases.image_cleanup import delete_pending_image
from cases.models import PendingImageDeletion


class Command(BaseCommand):
    help = "Retry pending image file deletions."

    def handle(self, *args, **options):
        failed = 0
        for pk in PendingImageDeletion.objects.values_list("pk", flat=True).iterator():
            if not delete_pending_image(pk):
                failed += 1
        if failed:
            raise CommandError(f"{failed} image deletions remain pending.")
        self.stdout.write(self.style.SUCCESS("Pending image deletions completed."))
