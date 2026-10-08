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
