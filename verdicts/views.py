from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import VerdictRequest
from .serializers import VerdictRequestSerializer
from .services import expire_if_stale


class VerdictRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, request_id):
        req = get_object_or_404(VerdictRequest, pk=request_id, user=request.user, case__deleted_at__isnull=True)
        expire_if_stale(req)
        req.refresh_from_db()
        return Response(VerdictRequestSerializer(req).data)


class CaseVerdictDetailView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, case_id):
        from cases.services import owned_case
        from .models import Verdict
        from .serializers import VerdictSerializer
        case = owned_case(request.user, case_id)
        verdict = get_object_or_404(
            Verdict.objects.select_related("case").prefetch_related(
                "factors__source", "reasons", "replies",
            ).filter(case=case, request__status="DONE").order_by("-id"),
        )
        return Response(VerdictSerializer(verdict).data)

class CaseShareView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, case_id):
        from django.urls import reverse
        from django.conf import settings
        from .publication import create_share
        from .serializers import PublicVerdictSerializer
        share = create_share(request.user, case_id)
        path = reverse("verdict-shared-detail", kwargs={"token": share.token})
        return Response({
            "token": str(share.token),
            "share_url": (
                f"{settings.FRONTEND_SHARE_BASE_URL.rstrip('/')}/{share.token}"
                if settings.FRONTEND_SHARE_BASE_URL else request.build_absolute_uri(path)
            ),
            "share_api_url": request.build_absolute_uri(path),
            "verdict": PublicVerdictSerializer(share.verdict).data,
        })
    def delete(self, request, case_id):
        from .publication import revoke_share
        revoke_share(request.user, case_id)
        return Response(status=204)

class SharedVerdictDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    def get(self, request, token):
        from .models import VerdictShare
        from .serializers import PublicVerdictSerializer
        share = get_object_or_404(
            VerdictShare.objects.select_related("verdict"),
            token=token, is_active=True,
            verdict__case__deleted_at__isnull=True, verdict__case__is_hidden=False,
            verdict__case__status="JUDGED", verdict__request__status="DONE",
        )
        return Response(PublicVerdictSerializer(share.verdict).data)

class CasePublishView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, case_id):
        from .publication import publish_case
        from .serializers import PublicVerdictSerializer
        case, verdict = publish_case(request.user, case_id)
        return Response({
            "case_id": case.pk, "is_public": case.is_public,
            "verdict": PublicVerdictSerializer(verdict).data,
        })
    def delete(self, request, case_id):
        from .publication import unpublish_case
        unpublish_case(request.user, case_id)
        return Response(status=204)
