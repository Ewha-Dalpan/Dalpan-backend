from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    nickname = models.CharField(max_length=50, blank=True)
    kakao_id = models.BigIntegerField(unique=True, null=True, blank=True)
