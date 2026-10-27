from rest_framework import serializers

from telerehabilitation_API.therapy.models import DifficultyRange
from telerehabilitation_API.therapy.serializers.exercise_skeleton_point_tracked import \
    ExerciseSkeletonPointTrackedSerializer


class DifficultyRangeSerializer(serializers.ModelSerializer):
    point_tracked = ExerciseSkeletonPointTrackedSerializer(required=False)

    class Meta:
        model = DifficultyRange
        fields = ['point_tracked_id', 'point_tracked', 'difficulty_id', 'max_angle', 'min_angle']
