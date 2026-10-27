from rest_framework import serializers

from telerehabilitation_API.therapy.models import Routine
from telerehabilitation_API.therapy.serializers import TherapySerializer
from telerehabilitation_API.therapy.serializers.exercise_serializer import ExerciseSerializer


class RoutineSerializer(serializers.ModelSerializer):
    exercises = ExerciseSerializer(many=True, required=False)
    therapy = TherapySerializer(required=False)
    therapy_id = serializers.IntegerField(required=False)

    class Meta:
        model = Routine
        fields = ['id', 'therapy', 'therapy_id', 'name', 'description', 'exercises', 'is_model', 'is_active']
