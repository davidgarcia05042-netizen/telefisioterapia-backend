from rest_framework import serializers

from telerehabilitation_API.therapy.models import ScheduledTraining
from telerehabilitation_API.therapy.serializers import RoutineSerializer, TherapyPatientSerializer


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


class ScheduledTrainingSerializer(serializers.ModelSerializer):
    routine = RoutineSerializer(required=False)
    therapy_patient = TherapyPatientSerializer(required=False)
    status = ChoiceField(choices=ScheduledTraining.SCHEDULED_TRAINING_STATUS, required=False)

    class Meta:
        model = ScheduledTraining
        fields = '__all__'
