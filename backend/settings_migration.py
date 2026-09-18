from models import Settings


def migrate_settings(raw):
    common = {k:v for k,v in raw.items() if k in Settings.model_fields and k != 'strategy'}
    return Settings(**common)