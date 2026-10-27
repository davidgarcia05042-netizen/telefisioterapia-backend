from django.db import models

from telerehabilitation_API.therapy.models import Routine, TherapyPatient


class ScheduledTraining(models.Model):
    NOT_STARTED = "1"
    STARTED = "2"
    FINISHED = "3"

    SCHEDULED_TRAINING_STATUS = [
        (NOT_STARTED, "Sin iniciar"),
        (STARTED, "Iniciado"),
        (FINISHED, "Finalizado")
    ]
    routine = models.ForeignKey(Routine, on_delete=models.SET_NULL, null=True, related_name='scheduled_trainings')
    therapy_patient = models.ForeignKey(
        TherapyPatient,
        on_delete=models.SET_NULL,
        null=True,
        related_name='scheduled_trainings'
    )
    start_time = models.DateTimeField(null=False)
    status = models.CharField(max_length=32, choices=SCHEDULED_TRAINING_STATUS, default=NOT_STARTED)
    active = models.BooleanField(default=True)
