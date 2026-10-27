"""
Contraseñas temporales desde la consola (B1, docs/DECISIONES_TECNICAS.md).

Las cuentas creadas antes de M1 tienen la contraseña por defecto del original
(LEGACY_DEFAULT_PASSWORD, en accounts.py). Este comando busca esas cuentas, les
asigna una contraseña temporal aleatoria, revoca su token (cierra sus
sesiones) e imprime la lista para entregársela a cada usuario. Con --user
emite una temporal nueva para usuarios concretos (por ejemplo, si alguien
perdió la que mostró el panel). Se corre en el shell de Render:

    python manage.py reset_default_passwords --dry-run      # solo lista los usuarios
    python manage.py reset_default_passwords                # las cuentas con la por defecto
    python manage.py reset_default_passwords --user ana --user luis

Las contraseñas solo se imprimen en esa salida: no se guardan en claro en
ningún lado.
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.authtoken.models import Token

from telerehabilitation_API.authentication.accounts import LEGACY_DEFAULT_PASSWORD, generate_temporary_password


class Command(BaseCommand):
    help = ('Asigna una contraseña temporal a las cuentas que siguen con la contraseña por defecto '
            'del original, o a los usuarios indicados con --user.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Solo lista los usuarios afectados, sin cambiar nada.')
        parser.add_argument('--user', action='append', default=[], metavar='USUARIO',
                            help='Usuario al que se le emite una temporal nueva (se puede repetir).')

    def handle(self, *args, **options):
        if options['user']:
            affected = list(User.objects.filter(username__in=options['user']).order_by('username'))
            unknown = sorted(set(options['user']) - {user.username for user in affected})
            if unknown:
                raise CommandError(f'No existen los usuarios: {", ".join(unknown)}.')
        else:
            affected = [user for user in User.objects.order_by('username')
                        if user.check_password(LEGACY_DEFAULT_PASSWORD)]
            if not affected:
                self.stdout.write('Ninguna cuenta tiene la contraseña por defecto.')
                return
        if options['dry_run']:
            self.stdout.write(f'{len(affected)} cuenta(s) afectada(s):')
            for user in affected:
                self.stdout.write(f'  {user.username}')
            return

        rows = []
        with transaction.atomic():
            for user in affected:
                password = generate_temporary_password()
                user.set_password(password)
                user.save(update_fields=['password'])
                Token.objects.filter(user=user).delete()
                rows.append((user.username, password))
        self.stdout.write(f'{len(rows)} cuenta(s) con contraseña temporal nueva. '
                          'Cópielas ahora: no se guardan en ningún lado.')
        width = max(len(username) for username, _ in rows)
        for username, password in rows:
            self.stdout.write(f'  {username.ljust(width)}  {password}')
