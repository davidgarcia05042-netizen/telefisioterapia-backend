from rest_framework import serializers

from telerehabilitation_API.therapy.models import ExerciseDifficulty
from telerehabilitation_API.therapy.serializers.difficulty_ranges_serializer import DifficultyRangeSerializer


class ExerciseDifficultySerializer(serializers.ModelSerializer):
    ranges = DifficultyRangeSerializer(many=True)

    class Meta:
        model = ExerciseDifficulty
        fields = ['id', 'exercise_id', 'name', 'description', 'ranges']
