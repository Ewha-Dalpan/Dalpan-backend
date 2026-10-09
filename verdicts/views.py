from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
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
