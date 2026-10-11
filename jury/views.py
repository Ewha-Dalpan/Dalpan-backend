from django.db.models import Count, OuterRef, Q, Subquery
from django.http import Http404, FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from verdicts.models import Verdict

from .models import CommentStatus, JuryComment
from .serializers import (
    CommentSerializer, JuryCaseListSerializer, ReportCreateSerializer, VoteCreateSerializer, image_url,
)
from .services import (
    can_view_result, create_comment, delete_comment, jury_cases, jury_result,
    my_vote, report_case, report_comment, submit_vote, viewable_case, public_images, 
)

RELATIONS = ["연애", "친구", "가족", "직장·학교", "기타"]


def _latest_verdict():
    return Verdict.objects.filter(case=OuterRef("pk"), request__status="DONE").order_by("-id")


class JuryPagination(PageNumberPagination):
    page_size = 20


class JuryCaseListView(generics.ListAPIView):
    """배심원석 목록. ?sort=latest|popular &relation=연애 &page="""
    permission_classes = [AllowAny]
    serializer_class = JuryCaseListSerializer
    pagination_class = JuryPagination

    def get_queryset(self):
        params = self.request.query_params
        qs = jury_cases().annotate(
            public_title=Subquery(_latest_verdict().values("public_title")[:1]),
            relation=Subquery(_latest_verdict().values("relation")[:1]),
            comment_count=Count("jury_comments", filter=Q(jury_comments__status=CommentStatus.VISIBLE)),
            thumbnail_id=Subquery(
                public_images().filter(case=OuterRef("pk")).order_by("sort_order", "id").values("id")[:1]
            ),
        )
        relation = params.get("relation")
        if relation in RELATIONS:
            qs = qs.filter(relation=relation)
        if params.get("sort") == "popular":
            return qs.order_by("-jury_count", "-public_at", "-id")
        return qs.order_by("-public_at", "-id")


class JuryCaseDetailView(APIView):
    """사건 페이지. 투표 전이면 저울, 투표 후(또는 작성자)면 결과+댓글 화면"""
    permission_classes = [AllowAny]

    def get(self, request, case_id):
        case = get_object_or_404(jury_cases(), pk=case_id)
        verdict = Verdict.objects.filter(case=case, request__status="DONE").order_by("-id").first()
        if verdict is None:
            raise Http404
        vote = my_vote(request.user, case)
        is_author = request.user.is_authenticated and case.user_id == request.user.pk
        viewable = can_view_result(request.user, case, vote)
        return Response({
            "case_id": case.pk,
            "relation": verdict.relation,
            "title": verdict.public_title,
            "summary": verdict.public_summary,
            "images": [image_url(request, case.pk, image.pk) for image in public_images().filter(case=case).order_by("sort_order", "id")],
            "public_at": case.public_at,
            "is_author": is_author,
            "can_vote": request.user.is_authenticated and not is_author and vote is None,
            "can_view_result": viewable,
            "my_vote": {"author": vote.fault_ratio, "other": 100 - vote.fault_ratio} if vote else None,
            "result": jury_result(case) if viewable else None,
            "jury_count": case.jury_count,
            "comment_count": case.jury_comments.filter(status=CommentStatus.VISIBLE).count(),
        })


class JuryVoteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        data = VoteCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        case, vote = submit_vote(user=request.user, case_id=case_id, fault_ratio=data.validated_data["fault_ratio"])
        return Response({
            "my_vote": {"author": vote.fault_ratio, "other": 100 - vote.fault_ratio},
            "result": jury_result(case),
            "jury_count": case.jury_count,
            "coin_rewarded": vote.coin_rewarded,
        }, status=status.HTTP_201_CREATED)


class JuryCommentListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CommentSerializer
    pagination_class = JuryPagination

    def get_queryset(self):
        case = viewable_case(self.request.user, self.kwargs["case_id"])
        return (JuryComment.objects.filter(case=case, status=CommentStatus.VISIBLE)
                .select_related("user").order_by("-created_at", "-id"))

    def create(self, request, *args, **kwargs):
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        comment = create_comment(user=request.user, case_id=kwargs["case_id"], content=data.validated_data["content"])
        return Response(self.get_serializer(comment).data, status=status.HTTP_201_CREATED)


class JuryCommentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, case_id, comment_id):
        delete_comment(user=request.user, case_id=case_id, comment_id=comment_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
    
class JuryCaseImageView(APIView):
    """공개 사건의 원본 이미지. <img src>로 바로 쓰도록 로그인 불필요, 공유 취소되면 404"""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, case_id, image_id):
        from PIL import Image, UnidentifiedImageError
        case = get_object_or_404(jury_cases(), pk=case_id)
        image = get_object_or_404(public_images(), case=case, pk=image_id)
        try:
            image_file = image.image_key.open("rb")
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
        response["Cache-Control"] = "no-store"  # 공유 취소 후 캐시로 계속 보이지 않게
        response["X-Content-Type-Options"] = "nosniff"
        return response


class JuryCaseReportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id):
        data = ReportCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        report_case(user=request.user, case_id=case_id, **data.validated_data)
        return Response(status=status.HTTP_201_CREATED)

class JuryCommentReportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, case_id, comment_id):
        data = ReportCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        report_comment(user=request.user, case_id=case_id, comment_id=comment_id, **data.validated_data)
        return Response(status=status.HTTP_201_CREATED)