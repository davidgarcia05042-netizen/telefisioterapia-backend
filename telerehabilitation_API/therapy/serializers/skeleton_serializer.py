from rest_framework import serializers

from telerehabilitation_API.therapy.models import SkeletonPoint


class SkeletonPointSerializer(serializers.ModelSerializer):

    class Meta:
        model = SkeletonPoint
        fields = '__all__'
