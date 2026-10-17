from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from coins.models import CoinWallet
from coins.services import VERDICT_COST
from verdicts.serializers import VerdictRequestSerializer

from .models import Case
from .serializers import CaseCreateSerializer, CaseSerializer, SituationUpdateSerializer
from .services import create_case, owned_case, request_judgment, submit_case, update_situation


class CasePagination(PageNumberPagination):
    page_size = 20


class CaseListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CaseSerializer
    pagination_class = CasePagination

    def get_queryset(self):
        qs = Case.objects.filter(user=self.request.user, deleted_at__isnull=True)
        if self.request.query_params.get('unfinished') == 'true':
            qs = qs.exclude(status=Case.Status.JUDGED)
        return qs.prefetch_related('images', 'situations', 'issues').order_by('-updated_at', '-id')

    def create(self, request, *args, **kwargs):
        data = CaseCreateSerializer(data=request.data)#request.data : 프론트에서 관계와 이미지를 받는다.
        data.is_valid(raise_exception=True)#입력이 올바른지 검사한다. 잘못됐으면 400에러 반환
        req = create_case(user=request.user, **data.validated_data) #validated_data : 유효성 검사를 통과한 데이터, create_case : 실제 DB 저장 함수 호출, request.user : 현재 로그인한 유저
        return Response(
            {'case_id': req.case_id, 'verdict_request': VerdictRequestSerializer(req).data},
            status=status.HTTP_201_CREATED,
        ) 


class CaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = owned_case(request.user, case_id)
        return Response(CaseSerializer(case, context={'request': request}).data)


class CasePaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, case_id):
        case = owned_case(request.user, case_id)
        balance = CoinWallet.objects.filter(user=request.user).values_list('balance', flat=True).first() or 0
        paid = case.verdict_requests.filter(status__in=['PENDING', 'RUNNING', 'AWAITING_CONFIRMATION']).exists()
        cost = 0 if paid else VERDICT_COST
        return Response({'balance': balance, 'cost': cost, 'can_submit': balance >= cost})


class CaseSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        req = submit_case(user=request.user, case_id=case_id)
        return Response(VerdictRequestSerializer(req).data, status=status.HTTP_202_ACCEPTED)


class SituationView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, case_id):
        data = SituationUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        case = update_situation(user=request.user, case_id=case_id, changes=data.validated_data)
        return Response(CaseSerializer(case, context={'request': request}).data)


class JudgmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        req = request_judgment(user=request.user, case_id=case_id)
        return Response(VerdictRequestSerializer(req).data, status=status.HTTP_202_ACCEPTED)


class CaseImageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def image_field(self, image):
        return image.image_key

    def get(self, request, case_id, image_id):
        from django.http import FileResponse, Http404
        from django.shortcuts import get_object_or_404
        from .models import CaseImage
        case = owned_case(request.user, case_id)
        image = get_object_or_404(CaseImage, case=case, pk=image_id)
        from PIL import Image, UnidentifiedImageError
        try:
            image_file = self.image_field(image).open("rb")
            try:
                with Image.open(image_file) as decoded:
                    content_type = Image.MIME.get(decoded.format, "application/octet-stream")
                image_file.seek(0)
                response = FileResponse(image_file, content_type=content_type)
            except Exception:
                image_file.close()
                raise
        except (FileNotFoundError, UnidentifiedImageError):
            raise Http404("이미지를 찾을 수 없습니다.") from None
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class CasePublicImageView(CaseImageDetailView):
    """Owner preview/upload/reset. Original is retained for analysis."""
    def image_field(self, image):
        return image.public_image or image.image_key

    def put(self, request, case_id, image_id):
        from rest_framework import serializers
        from .uploads import validate_case_images
        class UploadSerializer(serializers.Serializer):
            image = serializers.ImageField()
        payload = UploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data["image"]
        validate_case_images([data])
        image = self._update(request, case_id, image_id, data)
        from .serializers import CaseImageSerializer
        return Response(CaseImageSerializer(image, context={"request": request}).data)

    def delete(self, request, case_id, image_id):
        self._update(request, case_id, image_id, None)
        return Response(status=204)

    def _update(self, request, case_id, image_id, upload):
        from django.db import transaction
        from django.shortcuts import get_object_or_404
        from .models import CaseImage
        new_name = None
        storage = None
        try:
            with transaction.atomic():
                case = owned_case(request.user, case_id, lock=True)
                image = get_object_or_404(CaseImage.objects.select_for_update(), case=case, pk=image_id)
                old_name = image.public_image.name
                storage = image.public_image.storage
                from django.conf import settings
                from rest_framework.exceptions import ValidationError
                from .image_cleanup import schedule_image_deletion
                total = upload.size if upload is not None else image.image_key.size
                for other in case.images.exclude(pk=image.pk):
                    total += (other.public_image or other.image_key).size
                if total > settings.CASE_IMAGES_MAX_TOTAL_BYTES:
                    raise ValidationError({"image": "Public images exceed the total size limit."})
                if upload is None:
                    image.public_image = ""
                else:
                    image.public_image.save(upload.name, upload, save=False)
                    new_name = image.public_image.name
                image.save(update_fields=["public_image"])
                if old_name:
                    schedule_image_deletion(old_name)
                return image
        except Exception:
            if new_name:
                from .image_cleanup import schedule_image_deletion
                schedule_image_deletion(new_name)
            raise
