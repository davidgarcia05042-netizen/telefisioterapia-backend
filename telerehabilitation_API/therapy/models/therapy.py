from django.db import models


class Therapy(models.Model):
    name = models.CharField(max_length=128)
    description = models.TextField()
    is_model = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'therapy'
