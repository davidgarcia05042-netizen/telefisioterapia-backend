from rest_framework import serializers

from telerehabilitation_API.therapy.models import ExerciseResult
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint
from telerehabilitation_API.therapy.serializers import ExerciseSerializer
from telerehabilitation_API.therapy.serializers.exercise_results_point_serializer import ExerciseResultPointSerializer


class ChoiceField(serializers.ChoiceField):

    def to_representation(self, obj):
        if obj == '' and self.allow_blank:
            return obj
        return self._choices[obj]

    def to_internal_value(self, data):
        # To support inserts with the value
        if data == '' and self.allow_blank:
            return ''

        for key, val in self._choices.items():
            if val == data:
                return key
        self.fail('invalid_choice', input=data)


class ExerciseResultSerializer(serializers.ModelSerializer):
    exercise = ExerciseSerializer(read_only=True)
    training_id = serializers.IntegerField(read_only=True)
    status = ChoiceField(choices=ExerciseResult.EXERCISE_STATUS, required=False)
    video = serializers.CharField(required=False)
    points = ExerciseResultPointSerializer(many=True, read_only=True)

    class Meta:
        model = ExerciseResult
        # movement pesa varios MB: se sirve en GET /exercise_results/{id}/movement.
        exclude = ['movement']
