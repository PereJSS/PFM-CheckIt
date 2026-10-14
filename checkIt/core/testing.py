from unittest.mock import patch

from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle


def limitar(**tasas):
    """
    Sustituye tasas de throttling en los tests, p. ej. limitar(auth_login='2/min').
    override_settings no sirve: DRF lee THROTTLE_RATES al importar la clase.
    """
    rates = {**api_settings.DEFAULT_THROTTLE_RATES, **tasas}
    return patch.object(SimpleRateThrottle, 'THROTTLE_RATES', rates)
