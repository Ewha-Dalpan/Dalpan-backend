from datetime import timedelta
from io import BytesIO, StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.models import User
from coins.models import CoinWallet
from verdicts.models import VerdictRequest
from .models import Case, CaseImage
from .services import create_case
from .uploads import validate_case_images


def upload(fmt="PNG", size=(4, 4)):
    data = BytesIO()
    Image.new("RGB", size).save(data, format=fmt)
    return SimpleUploadedFile("chat." + fmt.lower(), data.getvalue())


class PrivateImageTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = override_settings(MEDIA_ROOT=self.temp.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = User.objects.create_user(username="image-owner", nickname="owner")
        self.other = User.objects.create_user(username="image-other", nickname="other")
        self.case = Case.objects.create(user=self.owner)
        self.image = CaseImage.objects.create(case=self.case, sort_order=0, image_key=upload())
        self.url = f"/api/cases/{self.case.pk}/images/{self.image.pk}/"
        self.client = APIClient()

    def test_owner_can_read_and_response_is_not_cacheable(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(b"".join(response.streaming_content), upload().read())
        response.close()
        detail = self.client.get(f"/api/cases/{self.case.pk}/")
        self.assertTrue(detail.data["images"][0]["image_key"].endswith(self.url))
        self.assertNotIn(self.image.image_key.name, detail.data["images"][0]["image_key"])

    def test_anonymous_and_other_owner_cannot_read(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_deleted_case_and_wrong_case_cannot_read(self):
        self.client.force_authenticate(self.owner)
        other_case = Case.objects.create(user=self.owner)
        self.assertEqual(self.client.get(
            f"/api/cases/{other_case.pk}/images/{self.image.pk}/"
        ).status_code, 404)
        self.case.deleted_at = timezone.now()
        self.case.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_missing_file_returns_404(self):
        self.image.image_key.storage.delete(self.image.image_key.name)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 404)


class UploadLimitsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="upload-owner", nickname="owner")
        self.wallet = CoinWallet.objects.create(user=self.owner, balance=1)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    @override_settings(CASE_IMAGE_MAX_BYTES=1)
    def test_rejected_upload_never_charges_or_stores(self):
        with patch("cases.services.get_dispatcher") as dispatcher:
            response = self.client.post("/api/cases/", {
                "relation": "연애", "images": [upload()]
            }, format="multipart")
            dispatcher.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Case.objects.exists())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1)

    @override_settings(CASE_IMAGES_MAX_TOTAL_BYTES=100)
    def test_total_limit_also_applies_to_service_calls(self):
        with patch("cases.services.get_dispatcher") as dispatcher:
            with self.assertRaises(ValidationError):
                create_case(user=self.owner, relation="연애", images=[upload(), upload()])
            dispatcher.assert_not_called()
        self.assertFalse(Case.objects.exists())

    @override_settings(CASE_IMAGE_MAX_PIXELS=10)
    def test_pixel_limit(self):
        with self.assertRaises(ValidationError):
            validate_case_images([upload()])

    def test_unsupported_and_corrupt_images(self):
        for image in [upload("GIF"), SimpleUploadedFile("fake.png", b"not an image")]:
            with self.subTest(name=image.name), self.assertRaises(ValidationError):
                validate_case_images([image])

    def test_valid_upload_is_rewound(self):
        image = upload()
        validate_case_images([image])
        self.assertEqual(image.tell(), 0)


@override_settings(CASE_IMAGE_RETENTION_DAYS=30)
class ImageCleanupTests(PrivateImageTests):
    def canceled(self):
        return VerdictRequest.objects.create(
            case=self.case, user=self.owner, status="CANCELED",
            finished_at=timezone.now() - timedelta(days=40),
        )

    def cleanup(self, apply=False):
        output = StringIO()
        call_command("cleanup_case_images", apply=apply, stdout=output, stderr=StringIO())
        return output.getvalue()

    def test_preview_and_apply_are_idempotent(self):
        self.canceled()
        name = self.image.image_key.name
        storage = self.image.image_key.storage
        self.assertIn("1개", self.cleanup())
        self.assertTrue(storage.exists(name))
        self.cleanup(True)
        self.assertFalse(storage.exists(name))
        self.assertFalse(CaseImage.objects.filter(pk=self.image.pk).exists())
        self.assertIn("0개", self.cleanup(True))

    def test_completed_and_active_cases_are_preserved(self):
        self.canceled()
        self.case.status = "JUDGED"
        self.case.save()
        self.cleanup(True)
        self.assertTrue(CaseImage.objects.filter(pk=self.image.pk).exists())
        self.case.status = "CONFIRMING"
        self.case.save()
        VerdictRequest.objects.create(case=self.case, user=self.owner, status="PENDING")
        self.cleanup(True)
        self.assertTrue(CaseImage.objects.filter(pk=self.image.pk).exists())

    def test_deleted_old_case_is_cleaned_but_recent_is_kept(self):
        self.case.deleted_at = timezone.now() - timedelta(days=1)
        self.case.save()
        self.cleanup(True)
        self.assertTrue(CaseImage.objects.filter(pk=self.image.pk).exists())
        self.case.deleted_at = timezone.now() - timedelta(days=40)
        self.case.save()
        self.cleanup(True)
        self.assertFalse(CaseImage.objects.filter(pk=self.image.pk).exists())

    def test_storage_failure_keeps_row_for_retry(self):
        self.canceled()
        with patch.object(self.image.image_key.storage, "delete", side_effect=OSError):
            with self.assertRaises(CommandError):
                self.cleanup(True)
        self.assertTrue(CaseImage.objects.filter(pk=self.image.pk).exists())
        self.cleanup(True)
        self.assertFalse(CaseImage.objects.filter(pk=self.image.pk).exists())

    @override_settings(CASE_IMAGE_RETENTION_DAYS=0)
    def test_disabled_retention_never_deletes(self):
        self.canceled()
        self.cleanup(True)
        self.assertTrue(CaseImage.objects.filter(pk=self.image.pk).exists())


class ProductionSettingsTests(SimpleTestCase):
    def load_settings(self, **changes):
        import os
        import runpy
        from django.conf import settings
        values = {
            "DEPLOYMENT_ENV": "production", "DEBUG": "False",
            "ALLOWED_HOSTS": "api.example.test",
            "SECRET_KEY": "test-only-secret", "IDENTITY_HASH_KEY": "test-only-identity",
            "DATABASE_URL": "postgres://test:test@localhost/test",
            "CASE_STORAGE_BACKEND": "s3", "AWS_STORAGE_BUCKET_NAME": "private-test-bucket",
            "CORS_ALLOWED_ORIGINS": "https://frontend.example.test",
            "FRONTEND_SHARE_BASE_URL": "https://frontend.example.test/shared",
            "SECURE_SSL_REDIRECT": "True", "SESSION_COOKIE_SECURE": "True",
            "CSRF_COOKIE_SECURE": "True", "TRUST_PROXY_HTTPS_HEADER": "True",
        }
        values.update(changes)
        with patch.dict(os.environ, values):
            return runpy.run_path(str(settings.BASE_DIR / "config" / "settings.py"))

    def test_production_uses_postgres_private_s3_and_domains(self):
        config = self.load_settings()
        self.assertEqual(config["DATABASES"]["default"]["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["CORS_ALLOWED_ORIGINS"], ["https://frontend.example.test"])
        self.assertEqual(config["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))
        self.assertTrue(config["SESSION_COOKIE_SECURE"])
        self.assertTrue(config["CSRF_COOKIE_SECURE"])
        options = config["STORAGES"]["default"]["OPTIONS"]
        from storages.backends.s3 import S3Storage
        storage = S3Storage(**options)
        self.assertEqual(storage.bucket_name, "private-test-bucket")
        self.assertIsNone(storage.default_acl)
        self.assertTrue(storage.querystring_auth)
        # Constructing the backend does not connect to AWS.
        import psycopg
        self.assertTrue(psycopg.__version__)

    def test_unsafe_production_settings_fail_at_startup(self):
        from django.core.exceptions import ImproperlyConfigured
        for values in [
            {"DATABASE_URL": "sqlite:///test.sqlite3"},
            {"DEBUG": "True"}, {"ALLOWED_HOSTS": ""},
            {"ALLOWED_HOSTS": "*"}, {"CASE_STORAGE_BACKEND": "local"},
            {"CASE_STORAGE_BACKEND": "typo"}, {"CASE_IMAGE_MAX_BYTES": "0"},
            {"CASE_IMAGE_RETENTION_DAYS": "-1"},
        ]:
            with self.subTest(values=values), self.assertRaises(ImproperlyConfigured):
                self.load_settings(**values)
