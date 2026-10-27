from rest_framework import serializers

from telerehabilitation_API.storage.backend import validate_public_video_url


class ExerciseResultsVideoSerializer(serializers.Serializer):
    # El backend descarga esta URL: solo se aceptan objetos del bucket propio (Supabase o R2).
    video = serializers.URLField(required=True, validators=[validate_public_video_url])
