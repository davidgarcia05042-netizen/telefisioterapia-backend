from rest_framework import serializers

from telerehabilitation_API.therapy.models import Therapy


class TherapySerializer(serializers.ModelSerializer):
    routines = serializers.HyperlinkedRelatedField(many=True, view_name='routine-detail', read_only=True)

    class Meta:
        model = Therapy
        fields = ['id', 'name', 'description', 'is_model', 'is_active', 'routines']
