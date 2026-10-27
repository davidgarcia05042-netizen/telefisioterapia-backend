from rest_framework import serializers

from telerehabilitation_API.therapy.models import ScheduledTrainingDifficulty
from telerehabilitation_API.therapy.serializers import ExerciseSerializer
from telerehabilitation_API.therapy.serializers.exercise_difficulty_serializer import ExerciseDifficultySerializer
from telerehabilitation_API.therapy.serializers.scheduled_training_serializer_partial import \
    ScheduledTrainingSerializerPartial


class ScheduledTrainingDiffSerializer(serializers.ModelSerializer):
    difficulty = ExerciseDifficultySerializer(read_only=True)
    exercise = ExerciseSerializer(read_only=True)
    scheduled_training = ScheduledTrainingSerializerPartial(read_only=True)

    class Meta:
        model = ScheduledTrainingDifficulty
        fields = ['difficulty', 'exercise', 'scheduled_training', 'scheduled_training_id']
