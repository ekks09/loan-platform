from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Platform core"

    def ready(self):
        from core import checks  # noqa: F401  (registers system checks)
