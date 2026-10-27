from rest_framework import serializers

from telerehabilitation_API.therapy.models import Exercise
from telerehabilitation_API.therapy.serializers.exercise_difficulty_serializer import ExerciseDifficultySerializer


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


class ExerciseSerializer(serializers.ModelSerializer):
    routine = serializers.HyperlinkedRelatedField(view_name='routine-detail', read_only=True)
    routine_id = serializers.IntegerField(required=False)
    status = ChoiceField(choices=Exercise.EXERCISE_STATUS, required=False)
    video = serializers.CharField(required=False)
    difficulties = ExerciseDifficultySerializer(many=True, required=False)

    class Meta:
        model = Exercise
        fields = ['id', 'name', 'description', 'order', 'routine', 'routine_id', 'status', 'video', 'is_model', 'is_active', 'difficulties']
