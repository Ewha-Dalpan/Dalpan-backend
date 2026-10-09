from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from coins.models import CoinWallet
from verdicts.models import VerdictRequest
from verdicts.services import complete_analysis, complete_request, fail_request, fail_stale_requests
from verdicts.tasks import AIUnavailable
from .models import Case, CaseImage, CaseSituation
from .services import create_case, submit_case, request_judgment, update_situation


class CaseFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', nickname='owner')
        self.other = User.objects.create_user(username='other', nickname='other')
        self.wallet = CoinWallet.objects.create(user=self.user, balance=1)
        self.case = Case.objects.create(user=self.user)
        CaseSituation.objects.create(case=self.case, relation='연애')
        CaseImage.objects.create(case=self.case, image_key='cases/test.png', sort_order=0)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def submit(self):
        with patch('cases.services.get_dispatcher', return_value=Mock()):
            return submit_case(user=self.user, case_id=self.case.pk)

    @override_settings(
        LINER_API_KEY="",
        VERDICT_ANALYSIS_DISPATCHER=None,
    )
    def test_unconfigured_ai_never_charges(self):
        with self.assertRaises(AIUnavailable):
            submit_case(user=self.user, case_id=self.case.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1)
        self.assertFalse(VerdictRequest.objects.exists())

    def analyzed(self):
        req = self.submit()
        self.assertTrue(complete_analysis(req.pk, summary='상황 요약', conflict_core='갈등 핵심'))
        return req

    @override_settings(
        LINER_API_KEY="",
        VERDICT_ANALYSIS_DISPATCHER=None,
    )
    def test_new_case_unconfigured_ai_stores_nothing(self):
        response = self.client.post(
            "/api/cases/",
            {
                "relation": "연애",
                "images": [self.image_upload()],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(Case.objects.count(), 1)
        self.assertFalse(VerdictRequest.objects.exists())

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1)

    def test_insufficient_balance_rolls_back(self):
        self.wallet.balance = 0
        self.wallet.save()
        with patch('cases.services.get_dispatcher', return_value=Mock()):
            response = self.client.post(f'/api/cases/{self.case.pk}/submit/')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(VerdictRequest.objects.exists())
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, 'WRITING')

    def test_dispatch_only_after_commit_and_duplicate_submit(self):
        dispatcher = Mock()
        with patch('cases.services.get_dispatcher', return_value=dispatcher):
            with self.captureOnCommitCallbacks(execute=True):
                req = submit_case(user=self.user, case_id=self.case.pk)
                dispatcher.assert_not_called()
                duplicate = submit_case(user=self.user, case_id=self.case.pk)
                self.assertEqual(req.pk, duplicate.pk)
            dispatcher.assert_called_once_with(req.pk)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 0)

    def test_save_resume_and_final_judgment_without_second_charge(self):
        req = self.analyzed()
        response = self.client.patch(f'/api/cases/{self.case.pk}/situation/',
                                     {'summary': '사용자 수정', 'user_speaker_side': 'LEFT'}, format='json')
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/api/cases/?unfinished=true')
        self.assertEqual(response.data['results'][0]['conflict_core'], '갈등 핵심')
        detail = self.client.get(f'/api/cases/{self.case.pk}/')
        self.assertEqual(detail.data['situation']['summary'], '사용자 수정')
        with patch('cases.services.get_dispatcher', return_value=Mock()):
            final = request_judgment(user=self.user, case_id=self.case.pk)
            self.assertEqual(req.pk, final.pk)
            duplicate = request_judgment(user=self.user, case_id=self.case.pk)
            self.assertEqual(final.pk, duplicate.pk)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 0)
        self.assertTrue(complete_request(final.pk, fault_ratio=50, judgment_text='판결', reasons=['근거']))
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, 'JUDGED')

    def test_core_and_other_users_are_protected(self):
        self.analyzed()
        response = self.client.patch(f'/api/cases/{self.case.pk}/situation/',
                                     {'conflict_core': '조작'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/cases/{self.case.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/cases/{self.case.pk}/submit/').status_code, 404)

    def test_confirmation_does_not_timeout(self):
        req = self.analyzed()
        VerdictRequest.objects.filter(pk=req.pk).update(started_at=timezone.now() - timedelta(days=2))
        self.assertEqual(fail_stale_requests(), 0)
        with patch('cases.services.get_dispatcher', return_value=Mock()):
            request_judgment(user=self.user, case_id=self.case.pk)
        self.assertEqual(fail_stale_requests(), 0)

    def test_analysis_failure_refunds_once_and_allows_resubmit(self):
        req = self.submit()
        self.assertTrue(fail_request(req.pk, '실패'))
        self.assertFalse(fail_request(req.pk, '중복 실패'))
        self.wallet.refresh_from_db()
        self.case.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1)
        self.assertEqual(self.case.status, 'WRITING')
        self.assertNotEqual(self.submit().pk, req.pk)

    def test_judgment_failure_refund_and_retry(self):
        self.analyzed()
        with patch('cases.services.get_dispatcher', return_value=Mock()):
            req = request_judgment(user=self.user, case_id=self.case.pk)
            fail_request(req.pk, '판결 실패')
            retry = request_judgment(user=self.user, case_id=self.case.pk)
        self.assertNotEqual(req.pk, retry.pk)
        self.assertEqual(retry.stage, 'JUDGMENT')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 0)

    def test_dispatch_failure_refunds(self):
        with patch('cases.services.get_dispatcher', return_value=Mock(side_effect=RuntimeError)):
            with self.captureOnCommitCallbacks(execute=True):
                req = submit_case(user=self.user, case_id=self.case.pk)
        req.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(req.status, 'FAILED')
        self.assertEqual(self.wallet.balance, 1)

    def test_stale_analysis_result_cannot_overwrite_user_edits(self):
        req = self.analyzed()
        update_situation(user=self.user, case_id=self.case.pk, changes={'summary': '사용자 수정'})
        self.assertFalse(complete_analysis(req.pk, summary='늦은 결과', conflict_core='다른 핵심'))
        self.assertEqual(self.case.situations.get().summary, '사용자 수정')

    def test_analysis_timeout_refunds_and_resets_draft(self):
        req = self.submit()
        VerdictRequest.objects.filter(pk=req.pk).update(started_at=timezone.now() - timedelta(days=1))
        self.assertEqual(fail_stale_requests(), 1)
        self.wallet.refresh_from_db()
        self.case.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1)
        self.assertEqual(self.case.status, 'WRITING')

    def image_upload(self, name='chat.png'):
        image = BytesIO()
        Image.new('RGB', (2, 2)).save(image, format='PNG')
        return SimpleUploadedFile(name, image.getvalue(), content_type='image/png')

    def test_multipart_image_create_and_default_speaker(self):
        dispatcher = Mock()
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            with patch('cases.services.get_dispatcher', return_value=dispatcher):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post('/api/cases/', {'relation': '연애', 'images': [self.image_upload()]}, format='multipart')
                    dispatcher.assert_not_called()
                    self.assertEqual(response.status_code, 201)
                    self.wallet.refresh_from_db()
                    self.assertEqual(self.wallet.balance, 0)
                request_id = response.data['verdict_request']['id']
                dispatcher.assert_called_once_with(request_id)
            detail = self.client.get('/api/cases/{}/'.format(response.data['case_id']))
            self.assertEqual(detail.data['status'], 'CONFIRMING')
            self.assertEqual(detail.data['situation']['relation'], '연애')
            self.assertEqual(detail.data['situation']['user_speaker_side'], 'RIGHT')
            self.assertEqual(len(detail.data['images']), 1)

    def test_new_case_insufficient_balance_stores_nothing(self):
        self.wallet.balance = 0
        self.wallet.save()
        with patch('cases.services.get_dispatcher', return_value=Mock()):
            with patch('django.db.models.fields.files.FieldFile.save') as upload:
                with self.captureOnCommitCallbacks() as callbacks:
                    response = self.client.post('/api/cases/', {'relation': '연애', 'images': [self.image_upload()]}, format='multipart')
                upload.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Case.objects.count(), 1)
        self.assertEqual(CaseSituation.objects.count(), 1)
        self.assertEqual(CaseImage.objects.count(), 1)
        self.assertFalse(VerdictRequest.objects.exists())
        self.assertEqual(callbacks, [])

    def test_upload_failure_rolls_back_coins_and_removes_files(self):
        from pathlib import Path
        original_save = CaseImage.save
        def fail_second_image(instance, *args, **kwargs):
            if instance.sort_order == 1:
                raise RuntimeError('image row failure')
            return original_save(instance, *args, **kwargs)
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            with patch('cases.services.get_dispatcher', return_value=Mock()):
                with patch.object(CaseImage, 'save', fail_second_image):
                    with self.captureOnCommitCallbacks() as callbacks:
                        with self.assertRaises(RuntimeError):
                            create_case(user=self.user, relation='연애',
                                        images=[self.image_upload(), self.image_upload('second.png')])
            self.assertFalse(any(path.is_file() for path in Path(media_root).rglob('*')))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1)
        self.assertEqual(Case.objects.count(), 1)
        self.assertEqual(CaseImage.objects.count(), 1)
        self.assertFalse(VerdictRequest.objects.exists())
        self.assertFalse(self.user.coin_ledgers.exists())
        self.assertEqual(callbacks, [])

    def test_create_requires_images(self):
        response = self.client.post('/api/cases/', {'relation': '연애'}, format='multipart')
        self.assertEqual(response.status_code, 400)
