from rest_framework import serializers

from telerehabilitation_API.therapy.models import ExerciseDifficulty, ExerciseSkeletonPointTracked
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint
from telerehabilitation_API.therapy.serializers.difficulty_ranges_serializer import DifficultyRangeSerializer
from telerehabilitation_API.therapy.serializers.exercise_skeleton_point_tracked import \
    ExerciseSkeletonPointTrackedSerializer


class ExerciseResultPointSerializer(serializers.ModelSerializer):
    point_tracked = ExerciseSkeletonPointTrackedSerializer(read_only=True)

    class Meta:
        model = ExerciseResultPoint
        fields = ['point_tracked', 'max_angle', 'min_angle', 'angle_series', 'error']
