from rest_framework import serializers

from telerehabilitation_API.therapy.models import Training
from telerehabilitation_API.therapy.serializers.exercise_results_serializer import ExerciseResultSerializer
from telerehabilitation_API.therapy.serializers.scheduled_training_serializer import ScheduledTrainingSerializer


class TrainingSerializer(serializers.ModelSerializer):
    schedule_training = ScheduledTrainingSerializer(required=False)
    results = ExerciseResultSerializer(read_only=True, many=True)

    class Meta:
        model = Training
        fields = '__all__'
